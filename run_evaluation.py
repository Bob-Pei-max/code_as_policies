"""Evaluate a frozen task/seed suite without retries or overwriting earlier trials."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import uuid

from src.evaluation import classify, digest, file_hash, load_suite, write_summary
from src.model_provider import QwenProgramProvider, load_model_config
from src.plan_execution import write_json

ROOT = Path(__file__).resolve().parent
SOURCE_FILES = ('run_evaluation.py', 'src/evaluation.py', 'run_planner.py', 'run_offline.py',
                'src/planning.py', 'src/model_provider.py', 'src/plan_execution.py',
                'src/upstream_env.py', 'build_env.py', 'upstream.json', 'environment.yml',
                'requirements-pip.txt', 'assets/manifest.json')
PHYSICAL_ARTIFACTS = ('demo.mp4', 'before.png', 'after.png', 'block_trace.csv',
                      'execution_result.json', 'initial_state.json', 'actions.jsonl')


def stop_process_tree(process):
    if os.name == 'nt':
        subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    process.wait(timeout=15)


def run_trial(command, log_path, timeout):
    with log_path.open('w', encoding='utf-8') as log:
        options = {'start_new_session': True} if os.name != 'nt' else {}
        with subprocess.Popen(command, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, **options) as process:
            try:
                return process.wait(timeout=timeout), False
            except subprocess.TimeoutExpired:
                stop_process_tree(process)
                return process.returncode, True
            except KeyboardInterrupt:
                stop_process_tree(process)
                raise


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--suite', type=Path, default=ROOT / 'examples/evaluation/block_to_bowl_v1.json')
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--model-api', action='store_true')
    mode.add_argument('--file-replay', action='store_true', help='Use reference programs; no language understanding or API.')
    parser.add_argument('--execute', action='store_true', help='Run physical simulation and evaluate independent goals.')
    parser.add_argument('--dry-run', action='store_true', help='Freeze inputs and write a manifest; no requests or simulation.')
    parser.add_argument('--model-config', type=Path, default=ROOT / 'config/model.json')
    parser.add_argument('--base-url')
    parser.add_argument('--model')
    parser.add_argument('--api-key-env', default='DASHSCOPE_API_KEY')
    parser.add_argument('--request-timeout', type=float, default=120)
    parser.add_argument('--max-completion-tokens', type=int, default=4096)
    parser.add_argument('--thinking', choices=('auto', 'enabled', 'disabled'), default='auto')
    parser.add_argument('--max-actions', type=int, choices=range(1, 5), default=4)
    parser.add_argument('--max-steps', type=int, default=48000)
    parser.add_argument('--simulation-timeout', type=float, default=180)
    parser.add_argument('--trial-timeout', type=float, default=420, help='Hard total timeout per trial, including all child processes.')
    parser.add_argument('--output', type=Path, help='Must not exist; defaults to outputs/evaluation_<unique id>.')
    args = parser.parse_args(argv)
    if (not 1 <= args.max_steps <= 48000
            or not math.isfinite(args.simulation_timeout) or not 0 < args.simulation_timeout <= 3600
            or not math.isfinite(args.trial_timeout) or not 0 < args.trial_timeout <= 7200):
        parser.error('Invalid simulation or total trial limits')
    try:
        suite = load_suite(args.suite, args.max_actions)
        model = None
        if args.model_api:
            config = load_model_config(args.model_config)
            provider = QwenProgramProvider(
                base_url=args.base_url or os.environ.get('CAP_MODEL_BASE_URL') or config['base_url'],
                model=args.model or os.environ.get('CAP_MODEL_NAME') or config['model'],
                api_key_env=args.api_key_env, timeout=args.request_timeout,
                max_completion_tokens=args.max_completion_tokens, thinking=args.thinking)
            model = provider.metadata
            if '<' in model['endpoint'] or '>' in model['endpoint']:
                raise ValueError('Configure CAP_MODEL_BASE_URL or --base-url; placeholder addresses cannot be evaluated')
            key = os.environ.get(args.api_key_env, '').strip()
            if not args.dry_run and (not key or any(ord(c) < 33 or ord(c) > 126 for c in key)):
                raise ValueError('Missing or invalid API key environment variable; no trial was started')
            if key and key in json.dumps({'suite': suite, 'model': model}, ensure_ascii=False):
                raise ValueError('Credential found in evaluation inputs; refusing to save or send them')
        if args.execute and not args.dry_run:
            for name in ('ur5e/ur5e.urdf', 'robotiq_2f_85/robotiq_2f_85.urdf', 'bowl/bowl.urdf'):
                if not (ROOT / 'assets' / name).is_file():
                    raise ValueError('Missing simulator assets; run download_assets.py first')
        out = (args.output or ROOT / 'outputs' / f'evaluation_{uuid.uuid4().hex[:12]}').resolve()
        out.mkdir(parents=True, exist_ok=False)
    except (ValueError, OSError) as exc:
        parser.error(str(exc))
    inputs = out / 'inputs'
    inputs.mkdir()
    frozen_hashes = {}
    for task in suite['tasks']:
        scene_path = inputs / f'{task["id"]}.json'
        write_json(scene_path, task['scene'])
        program_path = inputs / f'{task["id"]}.py'
        program_path.write_text(task['reference_program'], encoding='utf-8')
        for path in (scene_path, program_path):
            frozen_hashes[path.relative_to(out).as_posix()] = file_hash(path)
    revision = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=ROOT, capture_output=True, text=True, check=False)
    manifest = {
        'schema_version': 1, 'created_at_utc': datetime.now(timezone.utc).isoformat(),
        'suite': suite, 'suite_source_sha256': file_hash(args.suite),
        'mode': 'model_program_generation' if args.model_api else 'offline_program_replay',
        'execute': args.execute, 'dry_run': args.dry_run, 'model': model,
        'limits': {'max_actions': args.max_actions, 'max_steps': args.max_steps,
                   'simulation_timeout': args.simulation_timeout, 'trial_timeout': args.trial_timeout},
        'git_revision': revision.stdout.strip() if revision.returncode == 0 else None,
        'source_sha256': {name: file_hash(ROOT / name) for name in SOURCE_FILES},
        'input_sha256': frozen_hashes,
        'protocol': 'Seed-major order; one generation per trial; no retries; independent goals excluded from prompts.',
    }
    write_json(out / 'manifest.json', manifest)
    records = []
    for seed in suite['seeds']:
        for task in suite['tasks']:
            trial_id = f'{len(records) + 1:03d}_{task["id"]}_seed{seed}'
            records.append({'trial_id': trial_id, 'task_id': task['id'], 'seed': seed,
                            'status': 'not_run', 'output': trial_id})
    write_summary(out, manifest, records, 'prepared' if args.dry_run else 'running')
    print(f'Evaluation: {out}\nPlanned trials: {len(records)}; mode: {manifest["mode"]}; execute: {args.execute}', flush=True)
    if args.dry_run:
        return 0
    state, stop_reason = 'completed', None
    for index, record in enumerate(records):
        task = next(task for task in suite['tasks'] if task['id'] == record['task_id'])
        if (any(file_hash(ROOT / name) != sha for name, sha in manifest['source_sha256'].items())
                or any(file_hash(out / name) != sha for name, sha in frozen_hashes.items())):
            state, stop_reason = 'stopped', 'Source or frozen input changed; remaining trials were not run'
            break
        trial_out = out / record['output']
        command = [sys.executable, str(ROOT / 'run_planner.py'), '--task', task['task'],
                   '--scene', str(inputs / f'{task["id"]}.json'), '--seed', str(record['seed']),
                   '--max-actions', str(args.max_actions), '--max-steps', str(args.max_steps),
                   '--simulation-timeout', str(args.simulation_timeout), '--output', str(trial_out)]
        if args.model_api:
            command += ['--model-api', '--model', model['model'], '--base-url', model['endpoint'],
                        '--api-key-env', args.api_key_env, '--request-timeout', str(args.request_timeout),
                        '--max-completion-tokens', str(args.max_completion_tokens), '--thinking', args.thinking]
        else:
            command += ['--program-file', str(inputs / f'{task["id"]}.py')]
        if args.execute:
            command.append('--execute')
        record['status'] = 'running'
        write_summary(out, manifest, records, 'running')
        print(f'[{index + 1}/{len(records)}] {record["trial_id"]}: started', flush=True)
        start = time.perf_counter()
        try:
            returncode, timed_out = run_trial(command, out / f'{record["trial_id"]}.log', args.trial_timeout)
            record.update(returncode=returncode, wall_seconds=round(time.perf_counter() - start, 3))
            result_path = trial_out / 'result.json'
            if result_path.is_file():
                result = json.loads(result_path.read_text(encoding='utf-8'))
                record.update(status=classify(result, returncode, args.execute),
                              model_api_used=result.get('model_api_used', False),
                              plan_validated=result.get('plan_validated', False),
                              execution_success=result.get('execution', {}).get('execution_success'),
                              task_success=result.get('task_success'),
                              usage=result.get('provider', {}).get('usage', {}),
                              response_model=result.get('provider', {}).get('response_model'),
                              error=result.get('error') or result.get('execution', {}).get('error'))
                prompt = json.loads((trial_out / 'prompt.json').read_text(encoding='utf-8'))
                if digest(prompt) != task['prompt_sha256']:
                    record['status'] = 'protocol_mismatch'
                if (result.get('seed') != record['seed'] or result.get('task') != task['task']
                        or result.get('phase') != manifest['mode']):
                    record['status'] = 'protocol_mismatch'
                if (model and result.get('provider', {}).get('reply_accepted')
                        and record['response_model'] != model['model']):
                    record['status'] = 'protocol_mismatch'
                if result.get('execution_completed') and any(
                        not (trial_out / name).is_file() or (trial_out / name).stat().st_size == 0
                        for name in PHYSICAL_ARTIFACTS):
                    record['status'] = 'artifact_failure'
            else:
                record.update(status='runner_failure', error='No planner result; see trial log')
            if timed_out:
                record.update(status='trial_timeout', error='Trial process tree exceeded the total wall-clock limit')
        except KeyboardInterrupt:
            record['status'] = 'interrupted'
            state, stop_reason = 'interrupted', 'Interrupted by user; no automatic resume or retry'
        except Exception as exc:
            record.update(status='runner_failure', error=type(exc).__name__ + '; see retained trial artifacts')
        if record['status'] in ('api_failure', 'configuration_failure', 'protocol_mismatch', 'runner_failure'):
            state, stop_reason = 'stopped', 'Stopped after ' + record['status'] + '; remaining trials were not run'
        write_summary(out, manifest, records, state if state != 'completed' else 'running', stop_reason)
        print(f'[{index + 1}/{len(records)}] {record["trial_id"]}: {record["status"]}', flush=True)
        if state != 'completed':
            break
    summary = write_summary(out, manifest, records, state, stop_reason)
    print(json.dumps({key: summary[key] for key in ('state', 'completed', 'planned', 'successes', 'task_success_rate')}, indent=2), flush=True)
    return 0 if state == 'completed' and all(row['status'] in ('success', 'validated_only') for row in records) else 1


if __name__ == '__main__':
    raise SystemExit(main())
