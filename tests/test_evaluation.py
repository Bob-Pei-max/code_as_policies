"""No-network checks of fixed protocols, failure denominators and batch isolation."""
import contextlib
import copy
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import run_evaluation
from src.evaluation import classify, load_suite, summarize
from src.plan_execution import write_json
from src.planning import build_messages

ROOT = Path(__file__).resolve().parents[1]


class SuiteTests(unittest.TestCase):
    def test_default_suite_is_fifteen_prespecified_trials_with_paired_scenes(self):
        suite = load_suite(ROOT / 'examples/evaluation/block_to_bowl_v1.json')
        self.assertEqual(suite['seeds'], list(range(5)))
        self.assertEqual(len(suite['tasks']), 3)
        match, cross = suite['tasks'][1:]
        self.assertEqual(match['scene']['objects'], cross['scene']['objects'])
        self.assertNotEqual(match['scene']['goals'], cross['scene']['goals'])
        self.assertNotEqual(match['prompt_sha256'], cross['prompt_sha256'])

    def test_duplicate_seeds_and_tasks_or_missing_goals_are_rejected(self):
        original = json.loads((ROOT / 'examples/evaluation/block_to_bowl_v1.json').read_text(encoding='utf-8'))
        for task in original['tasks']:
            for name in ('scene', 'program_file'):
                task[name] = str((ROOT / 'examples/evaluation' / task[name]).resolve())
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'suite.json'
            for seeds in ([0, 0], [True], [-1], [2**32], []):
                suite = copy.deepcopy(original)
                suite['seeds'] = seeds
                write_json(path, suite)
                with self.subTest(seeds=seeds), self.assertRaises(ValueError):
                    load_suite(path)
            suite = copy.deepcopy(original)
            suite['tasks'].append(suite['tasks'][0])
            write_json(path, suite)
            with self.assertRaisesRegex(ValueError, 'unique'):
                load_suite(path)
            scene = Path(tmp) / 'no_goals.json'
            write_json(scene, {'objects': ['blue block', 'yellow bowl']})
            original['tasks'][0]['scene'] = str(scene)
            write_json(path, original)
            with self.assertRaisesRegex(ValueError, 'independent goals'):
                load_suite(path)

    def test_failure_stages_and_compile_only_are_distinct(self):
        result = {'phase': 'model_program_generation', 'model_api_used': True,
                  'provider': {'reply_accepted': True}, 'plan_validated': True,
                  'execution_completed': True, 'execution': {'execution_success': True},
                  'success': True, 'task_success': True}
        self.assertEqual(classify(result, 0, True), 'success')
        self.assertEqual(classify(result, 0, False), 'validated_only')
        for change, expected in [({'provider': {}}, 'api_failure'),
                                 ({'plan_validated': False}, 'plan_rejected'),
                                 ({'error': 'Simulation wall-clock limit exceeded'}, 'simulation_timeout'),
                                 ({'execution_completed': False}, 'execution_error'),
                                 ({'execution': {'execution_success': False}}, 'physical_failure'),
                                 ({'task_success': False}, 'task_failure'),
                                 ({'task_success': None}, 'runner_failure')]:
            with self.subTest(expected=expected):
                self.assertEqual(classify({**result, **change}, 1, True), expected)

    def test_failure_denominator_and_incomplete_batch_never_claim_full_success(self):
        rows = [{'task_id': 'single', 'status': 'success'},
                {'task_id': 'single', 'status': 'plan_rejected'},
                {'task_id': 'single', 'status': 'physical_failure'}]
        summary = summarize(rows, True)
        self.assertEqual(summary['completed'], 3)
        self.assertEqual(summary['task_success_rate'], 1 / 3)
        rows.append({'task_id': 'single', 'status': 'not_run'})
        self.assertIsNone(summarize(rows, True)['task_success_rate'])
        self.assertIsNone(summarize(rows[:1], False)['task_success_rate'])


class BatchTests(unittest.TestCase):
    def call(self, args):
        with contextlib.redirect_stdout(io.StringIO()):
            return run_evaluation.main(args)

    def suite(self, tmp):
        path = Path(tmp) / 'suite.json'
        write_json(path, {'version': 1, 'id': 'test_suite', 'description': 'offline test', 'seeds': [0, 1],
                          'tasks': [{'id': 'single', 'task': '把蓝色积木放进黄色碗',
                                     'scene': str(ROOT / 'examples/planning/single.json'),
                                     'program_file': str(ROOT / 'examples/planning/single.py')}]})
        return path

    def test_dry_run_saves_all_planned_trials_but_never_generates_or_simulates(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / 'batch'
            with patch('run_evaluation.run_trial', side_effect=AssertionError('No trial allowed')):
                self.assertEqual(self.call(['--suite', str(self.suite(tmp)), '--file-replay',
                                            '--execute', '--dry-run', '--output', str(out)]), 0)
            summary = json.loads((out / 'summary.json').read_text(encoding='utf-8'))
            self.assertEqual(summary['completed'], 0)
            self.assertEqual(summary['planned'], 2)
            self.assertIsNone(summary['task_success_rate'])

    def test_real_offline_compile_batch_keeps_goals_out_of_prompt_and_refuses_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / 'batch'
            args = ['--suite', str(self.suite(tmp)), '--file-replay', '--output', str(out)]
            self.assertEqual(self.call(args), 0)
            summary = json.loads((out / 'summary.json').read_text(encoding='utf-8'))
            self.assertEqual(summary['status_counts'], {'validated_only': 2})
            self.assertEqual(summary['api_requests_attempted'], 0)
            self.assertIsNone(summary['task_success_rate'])
            for row in summary['trials']:
                prompt = json.loads((out / row['output'] / 'prompt.json').read_text(encoding='utf-8'))
                self.assertNotIn('goals', prompt['messages'][1]['content'])
            before = (out / 'manifest.json').read_bytes()
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                self.call(args)
            self.assertEqual((out / 'manifest.json').read_bytes(), before)

    def test_changed_frozen_inputs_stop_batch_without_retry(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / 'batch'

            def fake_trial(command, log_path, timeout):
                output = Path(command[command.index('--output') + 1])
                output.mkdir()
                task = command[command.index('--task') + 1]
                write_json(output / 'prompt.json', {'messages': build_messages(task, ['blue block', 'yellow bowl'])})
                write_json(output / 'result.json', {'phase': 'offline_program_replay', 'task': task, 'seed': 0,
                                                  'model_api_used': False, 'plan_validated': True})
                (out / 'inputs/single.py').write_text('# changed', encoding='utf-8')
                return 0, False

            with patch('run_evaluation.run_trial', side_effect=fake_trial) as runner:
                self.assertEqual(self.call(['--suite', str(self.suite(tmp)), '--file-replay', '--output', str(out)]), 1)
            self.assertEqual(runner.call_count, 1)
            summary = json.loads((out / 'summary.json').read_text(encoding='utf-8'))
            self.assertEqual(summary['state'], 'stopped')
            self.assertEqual(summary['trials'][1]['status'], 'not_run')

    def test_model_credentials_are_not_written_during_preparation(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / 'batch'
            with patch.dict(os.environ, {'DASHSCOPE_API_KEY': 'test-credential-never-persist'}):
                self.assertEqual(self.call(['--suite', str(self.suite(tmp)), '--model-api', '--dry-run',
                                            '--base-url', 'https://example.com/v1', '--output', str(out)]), 0)
            for path in out.rglob('*'):
                if path.is_file():
                    self.assertNotIn(b'test-credential-never-persist', path.read_bytes())

    def test_plan_rejection_is_retained_and_next_trial_runs_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / 'batch'

            def fake_trial(command, log_path, timeout):
                output = Path(command[command.index('--output') + 1])
                output.mkdir()
                task = command[command.index('--task') + 1]
                seed = int(command[command.index('--seed') + 1])
                write_json(output / 'prompt.json', {'messages': build_messages(task, ['blue block', 'yellow bowl'])})
                write_json(output / 'result.json', {'phase': 'offline_program_replay', 'task': task, 'seed': seed,
                                                  'model_api_used': False, 'plan_validated': seed == 1})
                return (1 if seed == 0 else 0), False

            with patch('run_evaluation.run_trial', side_effect=fake_trial) as runner:
                self.assertEqual(self.call(['--suite', str(self.suite(tmp)), '--file-replay', '--output', str(out)]), 1)
            self.assertEqual(runner.call_count, 2)
            summary = json.loads((out / 'summary.json').read_text(encoding='utf-8'))
            self.assertEqual(summary['state'], 'completed')
            self.assertEqual(summary['status_counts'], {'plan_rejected': 1, 'validated_only': 1})
            self.assertIsNone(summary['task_success_rate'])

    def test_api_failure_stops_without_retry_and_does_not_count_pending_as_tested(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / 'batch'

            def fake_trial(command, log_path, timeout):
                output = Path(command[command.index('--output') + 1])
                output.mkdir()
                task = command[command.index('--task') + 1]
                write_json(output / 'prompt.json', {'messages': build_messages(task, ['blue block', 'yellow bowl'])})
                write_json(output / 'result.json', {'phase': 'model_program_generation', 'task': task, 'seed': 0,
                                                  'model_api_used': True, 'plan_validated': False,
                                                  'provider': {'reply_accepted': False, 'http_status': 401}})
                return 1, False

            with patch.dict(os.environ, {'DASHSCOPE_API_KEY': 'dummy-key'}), patch(
                    'run_evaluation.run_trial', side_effect=fake_trial) as runner:
                self.assertEqual(self.call(['--suite', str(self.suite(tmp)), '--model-api', '--execute',
                                            '--base-url', 'https://example.com/v1', '--output', str(out)]), 1)
            self.assertEqual(runner.call_count, 1)
            summary = json.loads((out / 'summary.json').read_text(encoding='utf-8'))
            self.assertEqual(summary['completed'], 1)
            self.assertEqual(summary['status_counts'], {'api_failure': 1, 'not_run': 1})
            self.assertEqual(summary['usage_missing_for_attempted_requests'], 1)
            self.assertIsNone(summary['task_success_rate'])

    def test_total_timeout_stops_process_tree(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch('run_evaluation.subprocess.Popen') as factory, patch('run_evaluation.stop_process_tree') as stop:
                process = factory.return_value.__enter__.return_value
                process.wait.side_effect = subprocess.TimeoutExpired('planner', 1)
                process.returncode = -9
                self.assertEqual(run_evaluation.run_trial(['planner'], Path(tmp) / 'trial.log', 1), (-9, True))
                stop.assert_called_once_with(process)


if __name__ == '__main__':
    unittest.main()
