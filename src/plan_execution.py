"""Trusted simulation worker: consumes validated action data, never source code."""
from __future__ import annotations

import argparse
import csv
import importlib.metadata
import json
import math
import os
from pathlib import Path
import platform
import time

from src.planning import Action, PlanError, validate_action, validate_objects

ROOT = Path(__file__).resolve().parents[1]


def write_json(path, value):
    # Never serialize non-finite measurements as if they were valid JSON numbers.
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')


def evaluate_goals(goals, measurements):
    """No independent goals means task success is unknown, not automatically true."""
    if not goals:
        return None
    return all(measurements[Action(**goal)]['success'] for goal in goals)


def run(request, out):
    import imageio.v2 as imageio
    import numpy as np
    import pybullet as p
    from run_offline import LocalEnv

    objects = request['objects']
    validate_objects(objects)
    actions = [validate_action(item['pick'], item['place'], objects) for item in request['actions']]
    goals = [validate_action(item['pick'], item['place'], objects) for item in request['goals']]
    if not 1 <= len(actions) <= 4 or len({a.pick for a in actions}) != len(actions):
        raise PlanError('Worker requires 1 to 4 actions with unique picks')
    blocks = [name for name in objects if name.endswith(' block')]
    max_steps = request['max_steps']
    if type(max_steps) is not int or not 1 <= max_steps <= 48000:
        raise PlanError('max_steps must be between 1 and 48000')
    report = {
        'success': False, 'task_success': None, 'execution_success': False,
        'seed': request['seed'], 'python': platform.python_version(),
        'dependencies': {name: importlib.metadata.version(name)
                         for name in ['numpy', 'pybullet', 'imageio', 'imageio-ffmpeg']},
        'criterion': 'official on_top_of AND max lift > 0.05 m; task success uses independent scene goals',
        'action_results': [], 'goal_results': [],
    }
    env = None
    original_cwd = Path.cwd()
    start = time.perf_counter()

    def positions():
        result = {name: env.get_obj_pos(name).tolist() for name in objects}
        if not all(math.isfinite(value) for xyz in result.values() for value in xyz):
            raise ValueError('Non-finite object position')
        return result

    def measurement(action, initial, trace):
        height_column = 3 + 3 * blocks.index(action.pick)
        lifted = max(row[height_column] for row in trace) - initial[action.pick][2]
        current = positions()
        distance = float(np.linalg.norm(np.array(current[action.pick])[:2] - np.array(current[action.place])[:2]))
        predicate = bool(env.on_top_of(action.pick, action.place))
        return {**action.to_dict(), 'max_lift_m': lifted, 'final_xy_distance_m': distance,
                'official_on_top_of': predicate, 'success': predicate and lifted > 0.05}

    try:
        for name in ['ur5e/ur5e.urdf', 'robotiq_2f_85/robotiq_2f_85.urdf', 'bowl/bowl.urdf']:
            if not (ROOT / 'assets' / name).is_file():
                raise FileNotFoundError(f'Missing asset: {name}. Run download_assets.py first.')
        os.chdir(ROOT / 'assets')
        np.random.seed(request['seed'])
        with imageio.get_writer(out / 'demo.mp4', fps=12, codec='libx264', quality=7) as writer:
            env = LocalEnv(writer, max_steps=max_steps, trace_objects=blocks)
            env.reset(objects)
            for _ in range(120):
                env.step_sim_and_render()
            before = positions()
            report['before'] = before
            write_json(out / 'initial_state.json', before)
            imageio.imwrite(out / 'before.png', env.get_camera_image())
            for _ in range(6):
                env.record_frame()
            with (out / 'actions.jsonl').open('w', encoding='utf-8') as action_log:
                for index, action in enumerate(actions):
                    action_before = positions()
                    trace_start = len(env.trace)
                    event = {'event': 'started', 'index': index, **action.to_dict(),
                             'physics_step': env.sim_step, 'before': action_before}
                    action_log.write(json.dumps(event, allow_nan=False) + '\n')
                    action_log.flush()
                    print(f'Action {index + 1}/{len(actions)}: {action.pick} -> {action.place}', flush=True)
                    env.step({'pick': env.get_obj_pos(action.pick)[:2].copy(),
                              'place': env.get_obj_pos(action.place)[:2].copy()})
                    for _ in range(480):
                        env.step_sim_and_render()
                    result = measurement(action, action_before, env.trace[trace_start:])
                    report['action_results'].append(result)
                    action_log.write(json.dumps({'event': 'finished', 'index': index,
                                                 'physics_step': env.sim_step, **result}, allow_nan=False) + '\n')
                    action_log.flush()
                    if not result['success']:
                        report['stopped_after_failed_action'] = index
                        break
            report['after'] = positions()
            imageio.imwrite(out / 'after.png', env.get_camera_image())
            for _ in range(12):
                env.record_frame()
            # Recheck at final state: a later placement may disturb an earlier one.
            measurements = {action: measurement(action, before, env.trace) for action in set(actions + goals)}
            report['final_action_results'] = [measurements[action] for action in actions]
            report['goal_results'] = [measurements[goal] for goal in goals]
            report['execution_success'] = (len(report['action_results']) == len(actions)
                                           and all(item['success'] for item in report['action_results'])
                                           and all(measurements[action]['success'] for action in actions))
            report['task_success'] = evaluate_goals(request['goals'], measurements)
            report['success'] = report['task_success'] if report['execution_success'] else False
    except Exception as exc:
        report['error'] = f'{type(exc).__name__}: {exc}'
    finally:
        if env is not None:
            report.update(physics_steps=env.sim_step, frames=env.frames)
            columns = ['physics_step'] + [f'{name.replace(" ", "_")}_{axis}_m' for name in blocks for axis in 'xyz']
            with (out / 'block_trace.csv').open('w', newline='', encoding='utf-8') as stream:
                writer = csv.writer(stream)
                writer.writerow(columns)
                writer.writerows(env.trace)
        if p.isConnected():
            p.disconnect()
        os.chdir(original_cwd)
        report['wall_seconds'] = round(time.perf_counter() - start, 3)
        write_json(out / 'execution_result.json', report)
    return 0 if report['execution_success'] and report['task_success'] is not False else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--request', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    return run(json.loads(args.request.read_text(encoding='utf-8')), args.output.resolve())


if __name__ == '__main__':
    raise SystemExit(main())
