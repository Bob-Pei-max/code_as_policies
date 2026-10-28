"""No-network tests of the program boundary, provenance, and failure reporting."""
import contextlib
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import run_planner
from src.plan_execution import evaluate_goals
from src.planning import (Action, FileProgramProvider, PlanError, build_messages,
                          compile_program, load_scene, normalize_program)

ROOT = Path(__file__).resolve().parents[1]
OBJECTS = ['blue block', 'red block', 'blue bowl', 'red bowl']


class ProgramTests(unittest.TestCase):
    def test_color_loop_expands_to_scene_actions(self):
        source = (ROOT / 'examples/planning/match_colors.py').read_text(encoding='utf-8')
        self.assertEqual(compile_program(source, OBJECTS),
                         (Action('blue block', 'blue bowl'), Action('red block', 'red bowl')))

    def test_changed_program_changes_order_and_destination(self):
        source = '''targets = ['red block', 'blue block']
for obj in targets:
    if get_color(obj) == 'red':
        put_first_on_second(obj, 'blue bowl')
    else:
        put_first_on_second(obj, 'red bowl')
'''
        self.assertEqual(compile_program(source, OBJECTS),
                         (Action('red block', 'blue bowl'), Action('blue block', 'red bowl')))

    def test_whole_program_rejected_for_forbidden_syntax(self):
        # Include dead branches: validation must inspect them before interpretation.
        bad = ["import os", "open('file')", "obj.__class__", "exec('bad')",
               "if is_block('blue block') and get_color('blue block') == 'blue':\n    get_obj_names()",
               "while 'yes':\n    put_first_on_second('blue block', 'blue bowl')",
               "def helper():\n    put_first_on_second('blue block', 'blue bowl')",
               "items = [x for x in get_obj_names()]", "x = get_obj_names()[0]",
               "if 'a' == 'b':\n    import os", "get_obj_names = 'replacement'",
               "for put_first_on_second in ['x']:\n    is_block('blue block')",
               "put_first_on_second(pick='blue block', place='blue bowl')",
               "x = put_first_on_second('blue block', 'blue bowl')"]
        for tail in bad:
            with self.subTest(source=tail), self.assertRaises(PlanError):
                compile_program("put_first_on_second('blue block', 'blue bowl')\n" + tail, OBJECTS)

    def test_nested_conditions_offer_supported_alternative_to_boolean_operators(self):
        source = '''for name in get_obj_names():
    if is_block(name):
        if get_color(name) == 'blue':
            put_first_on_second(name, 'red bowl')
'''
        self.assertEqual(compile_program(source, OBJECTS), (Action('blue block', 'red bowl'),))

    def test_invalid_actions_and_missing_helpers_are_rejected(self):
        for source in ["put_first_on_second('green block', 'blue bowl')",
                       "put_first_on_second('blue bowl', 'red bowl')",
                       "put_first_on_second('blue block', 'red block')",
                       "put_first_on_second(block, 'blue bowl')", "helper('blue block')",
                       "# no actions", "if 'non-boolean':\n    put_first_on_second('blue block', 'blue bowl')"]:
            with self.subTest(source=source), self.assertRaises(PlanError):
                compile_program(source, OBJECTS)

    def test_duplicate_actions_and_action_budget_are_rejected(self):
        with self.assertRaisesRegex(PlanError, 'Repeated picks'):
            compile_program("for obj in ['blue block', 'blue block']:\n    put_first_on_second(obj, 'blue bowl')", OBJECTS)
        with self.assertRaisesRegex(PlanError, 'action limit'):
            compile_program((ROOT / 'examples/planning/match_colors.py').read_text(), OBJECTS, max_actions=1)

    def test_source_ast_value_and_operation_limits(self):
        for source in ['#' * 16385, 'x = ' + repr('x' * 129),
                       'x = [' + ','.join(["'x'"] * 33) + ']',
                       'x = ' + '[' * 30 + "'x'" + ']' * 30,
                       "x = 'x'\n" * 300]:
            with self.subTest(source=source[:40]), self.assertRaises(PlanError):
                compile_program(source, OBJECTS)
        loops = "for a in get_obj_names():\n    for b in get_obj_names():\n        for c in get_obj_names():\n            for d in get_obj_names():\n                for e in get_obj_names():\n                    x = get_color(a)\n"
        with self.assertRaisesRegex(PlanError, 'budget'):
            compile_program(loops, OBJECTS)

    def test_fences_are_not_used_to_hide_extra_text(self):
        code = "put_first_on_second('blue block', 'blue bowl')"
        self.assertEqual(compile_program('```python\n' + code + '\n```', OBJECTS), (Action('blue block', 'blue bowl'),))
        with self.assertRaises(PlanError):
            normalize_program('```python\n' + code + '\n```\nexplanation')

    def test_file_replay_does_not_interpret_a_changed_task(self):
        provider = FileProgramProvider(ROOT / 'examples/planning/single.py')
        first = provider.generate(build_messages('把蓝色积木放进黄色碗', ['blue block', 'yellow bowl']))
        second = provider.generate(build_messages('Do something different', ['blue block', 'yellow bowl']))
        self.assertEqual(first.text, second.text)
        self.assertFalse(first.model_api_used)
        self.assertFalse(first.metadata['task_interpreted_by_model'])
        self.assertEqual(len(first.metadata['sha256']), 64)

    def test_scene_rejects_duplicate_or_unsupported_objects_and_conflicting_goals(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'scene.json'
            for scene in [
                {'objects': ['blue block', 'blue block']},
                {'objects': ['invisible block', 'red bowl']},
                {'objects': OBJECTS, 'goals': [{'pick': 'blue block', 'place': 'blue bowl'},
                                              {'pick': 'blue block', 'place': 'red bowl'}]},
                {'objects': OBJECTS, 'goals': [{'pick': 'red block', 'place': 'yellow bowl'}]},
            ]:
                path.write_text(json.dumps(scene), encoding='utf-8')
                with self.subTest(scene=scene), self.assertRaises(PlanError):
                    load_scene(path)

    def test_independent_goals_can_fail_even_when_generated_actions_succeed(self):
        measurements = {Action('blue block', 'red bowl'): {'success': True},
                        Action('blue block', 'blue bowl'): {'success': False}}
        self.assertFalse(evaluate_goals([{'pick': 'blue block', 'place': 'blue bowl'}], measurements))
        self.assertIsNone(evaluate_goals([], measurements))


class EntryPointTests(unittest.TestCase):
    def args(self, output, program=None):
        return ['--task', '把蓝色积木放进黄色碗', '--scene', str(ROOT / 'examples/planning/single.json'),
                '--program-file', str(program or ROOT / 'examples/planning/single.py'), '--output', str(output)]

    def call(self, args):
        with contextlib.redirect_stdout(io.StringIO()):
            return run_planner.main(args)

    def test_compile_only_saves_provenance_without_simulation_or_claiming_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / 'run'
            with patch('run_planner.subprocess.run', side_effect=AssertionError('Must not simulate')):
                self.assertEqual(self.call(self.args(out)), 0)
            result = json.loads((out / 'result.json').read_text(encoding='utf-8'))
            prompt = json.loads((out / 'prompt.json').read_text(encoding='utf-8'))
            self.assertIsNone(result['success'])
            self.assertFalse(result['model_api_used'])
            self.assertTrue(result['plan_validated'])
            self.assertNotIn('goals', json.loads(prompt['messages'][1]['content']))
            self.assertIn('把蓝色积木', prompt['messages'][1]['content'])
            self.assertFalse((out / 'demo.mp4').exists())

    def test_invalid_program_never_starts_simulation_and_retains_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / 'run'
            program = Path(tmp) / 'bad.py'
            program.write_text("put_first_on_second('blue block', 'yellow bowl')\nimport os\n")
            with patch('run_planner.subprocess.run', side_effect=AssertionError('Must not simulate')):
                self.assertEqual(self.call(self.args(out, program) + ['--execute']), 1)
            result = json.loads((out / 'result.json').read_text(encoding='utf-8'))
            self.assertFalse(result['plan_validated'])
            self.assertFalse(result['success'])
            self.assertTrue((out / 'program.py').exists())

    def test_timeout_retains_plan_and_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / 'run'
            with patch('run_planner.subprocess.run', side_effect=subprocess.TimeoutExpired('simulation', 1)):
                self.assertEqual(self.call(self.args(out) + ['--execute']), 1)
            result = json.loads((out / 'result.json').read_text(encoding='utf-8'))
            self.assertIn('wall-clock', result['error'])
            self.assertTrue(result['plan_validated'])
            self.assertFalse(result['execution_completed'])

    def test_existing_output_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / 'run'
            self.call(self.args(out))
            original = (out / 'result.json').read_bytes()
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                self.call(self.args(out))
            self.assertEqual(original, (out / 'result.json').read_bytes())


if __name__ == '__main__':
    unittest.main()
