"""Generate or replay a bounded program, then optionally run the CaP simulator."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid

from src.planning import FileProgramProvider, build_messages, compile_program, load_scene, normalize_program
from src.plan_execution import write_json
from src.model_provider import QwenProgramProvider, load_model_config

ROOT = Path(__file__).resolve().parent


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--task', required=True, help='Task sent to the model; file replay ignores its meaning.')
    parser.add_argument('--scene', type=Path, required=True, help='JSON with objects and optional independent goals.')
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument('--program-file', type=Path, help='Offline code fixture, never exec/eval-ed.')
    source.add_argument('--model-api', action='store_true', help='Generate code via the configured Qwen HTTPS API.')
    parser.add_argument('--model-config', type=Path, default=ROOT / 'config' / 'model.json')
    parser.add_argument('--base-url', help='Overrides CAP_MODEL_BASE_URL and model config.')
    parser.add_argument('--model', help='Overrides CAP_MODEL_NAME and model config.')
    parser.add_argument('--api-key-env', default='DASHSCOPE_API_KEY', help='Environment variable NAME, never the key value.')
    parser.add_argument('--request-timeout', type=float, default=120, help='Network socket timeout in seconds (maximum 300).')
    parser.add_argument('--max-completion-tokens', type=int, default=4096, help='Limit total reasoning and answer tokens.')
    parser.add_argument('--thinking', choices=('auto', 'enabled', 'disabled'), default='auto')
    parser.add_argument('--execute', action='store_true', help='Run validated actions in PyBullet; otherwise only compile.')
    parser.add_argument('--seed', type=int, default=0)
    parser.add_argument('--max-actions', type=int, choices=range(1, 5), default=4)
    parser.add_argument('--max-steps', type=int, default=48000)
    parser.add_argument('--simulation-timeout', type=float, default=180, help='Worker wall-clock limit in seconds, including scene reset.')
    parser.add_argument('--output', type=Path, help='Must not exist; default is a unique outputs/planning_* directory.')
    args = parser.parse_args(argv)
    if not 0 <= args.seed < 2**32:
        parser.error('seed must be between 0 and 2**32 - 1')
    if not 1 <= args.max_steps <= 48000:
        parser.error('max-steps must be between 1 and 48000')
    if not math.isfinite(args.simulation_timeout) or not 0 < args.simulation_timeout <= 3600:
        parser.error('simulation-timeout must be greater than 0 and at most 3600')
    out = (args.output or ROOT / 'outputs' / f'planning_{uuid.uuid4().hex[:12]}').resolve()
    try:
        out.mkdir(parents=True, exist_ok=False)
    except FileExistsError:
        parser.error(f'Output already exists; choose a new directory: {out}')
    report = {
        'phase': 'model_program_generation' if args.model_api else 'offline_program_replay',
        'model_api_used': False,
        'task': args.task, 'seed': args.seed, 'plan_validated': False,
        'execution_requested': args.execute, 'execution_completed': False,
        'success': None, 'task_success': None,
        'source_commit': json.loads((ROOT / 'upstream.json').read_text(encoding='utf-8'))['commit'],
        'started_at_utc': datetime.now(timezone.utc).isoformat(),
        'limits': {'max_actions': args.max_actions, 'max_steps': args.max_steps,
                   'simulation_timeout_seconds': args.simulation_timeout},
    }
    start = time.perf_counter()
    code = 1
    provider = None
    try:
        scene = load_scene(args.scene)
        write_json(out / 'scene.json', scene)
        # Goals are evaluation labels, deliberately excluded from the model's input.
        messages = build_messages(args.task, scene['objects'], args.max_actions)
        write_json(out / 'prompt.json', {'messages': messages})
        if args.model_api:
            config = load_model_config(args.model_config)
            provider = QwenProgramProvider(
                base_url=args.base_url or os.environ.get('CAP_MODEL_BASE_URL') or config['base_url'],
                model=args.model or os.environ.get('CAP_MODEL_NAME') or config['model'],
                api_key_env=args.api_key_env, timeout=args.request_timeout,
                max_completion_tokens=args.max_completion_tokens, thinking=args.thinking,
            )
        else:
            provider = FileProgramProvider(args.program_file)
        reply = provider.generate(messages)
        report.update(provider=reply.metadata, model_api_used=reply.model_api_used)
        (out / 'response.txt').write_text(reply.text, encoding='utf-8')
        source = normalize_program(reply.text)
        (out / 'program.py').write_text(source, encoding='utf-8')
        # Explicit artifact: helper synthesis is unsupported in this first stage.
        write_json(out / 'helper_functions.json', {'supported': False, 'sources': {}})
        actions = compile_program(source, scene['objects'], args.max_actions)
        plan = [action.to_dict() for action in actions]
        write_json(out / 'plan.json', plan)
        report.update(plan_validated=True, actions=plan,
                      program_sha256=hashlib.sha256(source.encode('utf-8')).hexdigest())
        if args.execute:
            request = {**scene, 'actions': plan, 'seed': args.seed, 'max_steps': args.max_steps}
            write_json(out / 'execution_request.json', request)
            # Timeout also covers the upstream rejection sampler during scene reset.
            # This process runs trusted code and action data, not generated Python.
            with (out / 'simulation.log').open('w', encoding='utf-8') as log:
                completed = subprocess.run(
                    [sys.executable, '-m', 'src.plan_execution', '--request', str(out / 'execution_request.json'),
                     '--output', str(out)], cwd=ROOT, stdout=log, stderr=subprocess.STDOUT,
                    timeout=args.simulation_timeout, check=False,
                )
            report['worker_exit_code'] = completed.returncode
            if not (out / 'execution_result.json').is_file():
                raise RuntimeError('Simulation worker produced no report; see simulation.log')
            execution = json.loads((out / 'execution_result.json').read_text(encoding='utf-8'))
            report.update(execution=execution, success=execution['success'], task_success=execution['task_success'],
                          execution_completed='error' not in execution)
            code = completed.returncode
        else:
            code = 0
    except subprocess.TimeoutExpired:
        report.update(success=False, error='Simulation wall-clock limit exceeded; partial artifacts retained')
    except Exception as exc:
        report.update(success=False, error=f'{type(exc).__name__}: {exc}')
    finally:
        if isinstance(provider, QwenProgramProvider):
            report.update(provider=provider.metadata,
                          model_api_used=provider.metadata['api_call_attempted'])
            if provider.response_text is not None:
                (out / 'response.txt').write_text(provider.response_text, encoding='utf-8')
        report['wall_seconds'] = round(time.perf_counter() - start, 3)
        write_json(out / 'result.json', report)
    print(json.dumps({'output': str(out), 'phase': report['phase'], 'plan_validated': report['plan_validated'],
                      'success': report['success'], 'model_api_used': report['model_api_used'],
                      'error': report.get('error')}, ensure_ascii=False, indent=2))
    return code


if __name__ == '__main__':
    raise SystemExit(main())
