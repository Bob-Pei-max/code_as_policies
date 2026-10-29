"""Fixed-suite input validation and honest aggregation of planner trial reports."""
from collections import Counter
import csv
import hashlib
import json
from pathlib import Path
import re

from src.planning import build_messages, compile_program, load_scene


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    allow_nan=False).encode('utf-8')).hexdigest()


def file_hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load_suite(path, max_actions=4):
    path = Path(path).resolve()
    suite = json.loads(path.read_text(encoding='utf-8-sig'))
    if (not isinstance(suite, dict) or set(suite) != {'version', 'id', 'description', 'seeds', 'tasks'}
            or type(suite['version']) is not int or suite['version'] != 1):
        raise ValueError('Expected evaluation suite version 1 with id, description, seeds and tasks')
    valid_id = lambda value: isinstance(value, str) and re.fullmatch(r'[a-z][a-z0-9_]{0,47}', value)
    if not valid_id(suite['id']) or not isinstance(suite['description'], str):
        raise ValueError('Invalid suite id or description')
    seeds = suite['seeds']
    if (not isinstance(seeds, list) or not 1 <= len(seeds) <= 100
            or any(type(seed) is not int or not 0 <= seed < 2**32 for seed in seeds)
            or len(set(seeds)) != len(seeds)):
        raise ValueError('Expected 1 to 100 unique integer seeds in [0, 2**32)')
    if not isinstance(suite['tasks'], list) or not 1 <= len(suite['tasks']) <= 32:
        raise ValueError('Expected 1 to 32 tasks')
    tasks, ids = [], set()
    for task in suite['tasks']:
        if not isinstance(task, dict) or set(task) != {'id', 'task', 'scene', 'program_file'}:
            raise ValueError('Each task needs id, task, scene and program_file')
        if not valid_id(task['id']) or task['id'] in ids:
            raise ValueError('Task ids must be unique lowercase identifiers')
        ids.add(task['id'])
        if not isinstance(task['task'], str):
            raise ValueError('Task instruction must be text')
        if any(not isinstance(task[key], str) or not task[key] for key in ('scene', 'program_file')):
            raise ValueError('Scene and program_file must be paths relative to the suite file')
        scene = load_scene(path.parent / task['scene'])
        if not scene['goals']:
            raise ValueError('Every evaluated task must have independent goals')
        prompt = build_messages(task['task'], scene['objects'], max_actions)
        program = (path.parent / task['program_file']).read_text(encoding='utf-8')
        # Validate reference fixtures up front; never use them as a model fallback.
        reference = compile_program(program, scene['objects'], max_actions)
        if {(a.pick, a.place) for a in reference} != {(g['pick'], g['place']) for g in scene['goals']}:
            raise ValueError('Reference program must target exactly the independent goals')
        tasks.append({'id': task['id'], 'task': task['task'], 'scene': scene,
                      'reference_program': program, 'prompt_sha256': digest({'messages': prompt})})
    return {**suite, 'tasks': tasks}


def classify(result, returncode, execute):
    """Only complete physical execution plus independent goals counts as success."""
    provider = result.get('provider', {})
    if result.get('phase') == 'model_program_generation' and not provider.get('reply_accepted'):
        return 'api_failure' if result.get('model_api_used') else 'configuration_failure'
    if not result.get('plan_validated'):
        return 'plan_rejected'
    if not execute:
        return 'validated_only' if returncode == 0 else 'runner_failure'
    if 'wall-clock limit' in result.get('error', ''):
        return 'simulation_timeout'
    execution = result.get('execution', {})
    if not result.get('execution_completed') or 'error' in execution:
        return 'execution_error'
    if execution.get('execution_success') is not True:
        return 'physical_failure'
    if result.get('task_success') is False:
        return 'task_failure'
    if result.get('task_success') is not True or returncode != 0 or result.get('success') is not True:
        return 'runner_failure'
    return 'success'


def summarize(records, execute):
    def counts(rows):
        statuses = Counter(row['status'] for row in rows)
        completed = sum(n for key, n in statuses.items() if key not in ('not_run', 'running', 'interrupted'))
        successful = statuses.get('success', 0)
        # An incomplete or compile-only batch never gets an overall task success rate.
        return {'planned': len(rows), 'completed': completed, 'successes': successful,
                'task_success_rate': successful / len(rows) if execute and completed == len(rows) else None,
                'status_counts': dict(statuses)}
    usage = Counter()
    for row in records:
        usage.update(row.get('usage', {}))
    return {**counts(records), 'by_task': {
                name: counts([row for row in records if row['task_id'] == name])
                for name in dict.fromkeys(row['task_id'] for row in records)},
            'api_requests_attempted': sum(row.get('model_api_used', False) for row in records),
            'trials_without_api_attempt_metadata': sum(
                row['status'] not in ('not_run', 'running') and 'model_api_used' not in row for row in records),
            'reported_usage': dict(usage),
            'usage_missing_for_attempted_requests': sum(
                bool(row.get('model_api_used')) and 'total_tokens' not in row.get('usage', {}) for row in records)}


def write_summary(out, manifest, records, state, stop_reason=None):
    from src.plan_execution import write_json
    summary = {'suite_id': manifest['suite']['id'], 'mode': manifest['mode'], 'state': state,
               'execute': manifest['execute'], 'manifest_sha256': file_hash(out / 'manifest.json'),
               'stop_reason': stop_reason, **summarize(records, manifest['execute']), 'trials': records}
    # Atomic replacement leaves the previous complete summary readable after interruption.
    pending = out / 'summary.pending.json'
    write_json(pending, summary)
    pending.replace(out / 'summary.json')
    fields = ('trial_id', 'task_id', 'seed', 'status', 'model_api_used', 'plan_validated',
              'execution_success', 'task_success', 'returncode', 'wall_seconds', 'output')
    with (out / 'trials.csv').open('w', newline='', encoding='utf-8-sig') as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction='ignore')
        writer.writeheader()
        writer.writerows(records)
    lines = [f'# Evaluation: {summary["suite_id"]}', '',
             f'Mode: {summary["mode"]}; state: {state}; completed: {summary["completed"]}/{summary["planned"]}.',
             'Development baseline only; not a reproduction of the paper benchmark.', '',
             '| Task | Success / completed / planned | Task success rate |',
             '| --- | --- | --- |']
    for task, row in summary['by_task'].items():
        rate = 'not available' if row['task_success_rate'] is None else f'{row["task_success_rate"]:.1%}'
        lines.append(f'| {task} | {row["successes"]} / {row["completed"]} / {row["planned"]} | {rate} |')
    lines += ['', 'All planned trials (including failures and trials not run) remain in summary.json and trials.csv.',
              'Scene seeds change simulator placement, not the model sampling seed. Goals are excluded from prompts.',
              'No retries, response editing, or hand-written fallback. Missing API usage is not counted as zero cost.', '']
    (out / 'summary.md').write_text('\n'.join(lines), encoding='utf-8')
    return summary
