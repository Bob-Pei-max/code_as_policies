"""Phase 1: manual pick-and-place using the official CaP physics environment.

No language model, no API key, no network access. Produces video and state checks.
"""
from __future__ import annotations

import argparse
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import sys
import time

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / 'src'))
import imageio.v2 as imageio
import numpy as np
import pybullet as p
from upstream_env import PickPlaceEnv


class LocalEnv(PickPlaceEnv):
    """Original physics plus deterministic mimic joints and desktop recording."""

    def __init__(self, writer, max_steps=48000):
        super().__init__(render=False)
        self.writer = writer
        self.max_steps = max_steps
        self.frames = 0
        self.trace = []

    def get_camera_image(self):
        view = p.computeViewMatrix([0.72, -1.15, 0.72], [0, -0.42, 0.10], [0, 0, 1])
        projection = p.computeProjectionMatrixFOV(55, 640 / 480, 0.01, 5)
        _, _, rgba, _, _ = p.getCameraImage(640, 480, view, projection, shadow=1, renderer=p.ER_TINY_RENDERER)
        return np.asarray(rgba, dtype=np.uint8).reshape(480, 640, 4)[:, :, :3]

    def record_frame(self):
        self.writer.append_data(self.get_camera_image())
        self.frames += 1

    def step_sim_and_render(self):
        if self.sim_step >= self.max_steps:
            raise TimeoutError(f'Motion exceeded {self.max_steps} physics steps')
        # Same mimic-joint equation as the upstream notebook, without a wall-clock thread.
        if self.gripper is not None:
            angle = p.getJointState(self.gripper.body, 1)[0]
            p.setJointMotorControlArray(
                self.gripper.body, [6, 3, 8, 5, 10], p.POSITION_CONTROL,
                [angle, -angle, -angle, angle, angle], positionGains=np.ones(5),
            )
        p.stepSimulation()
        self.sim_step += 1
        block = self.get_obj_pos('blue block').tolist()
        self.trace.append([self.sim_step, *block])
        if self.sim_step % 40 == 0:
            self.record_frame()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--seed', type=int, default=0)
    parser.add_argument('--output', type=Path, default=ROOT / 'outputs' / 'offline_seed0')
    args = parser.parse_args()
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=True)
    for name in ['ur5e/ur5e.urdf', 'robotiq_2f_85/robotiq_2f_85.urdf', 'bowl/bowl.urdf']:
        if not (ROOT / 'assets' / name).is_file():
            raise FileNotFoundError(f'Missing asset: {name}. Run download_assets.py first.')
    os.chdir(ROOT / 'assets')
    np.random.seed(args.seed)
    env = None
    report = {'phase': 'offline_manual_control', 'model_api_used': False, 'seed': args.seed,
              'task': 'put the blue block on the yellow bowl',
              'source_commit': json.loads((ROOT / 'upstream.json').read_text())['commit'],
              'python': platform.python_version(),
              'dependencies': {name: importlib.metadata.version(name) for name in ['numpy', 'imageio', 'imageio-ffmpeg']}}
    start = time.perf_counter()
    success = False
    try:
        with imageio.get_writer(out / 'demo.mp4', fps=12, codec='libx264', quality=7) as writer:
            env = LocalEnv(writer)
            print('Initializing official UR5e / Robotiq scene ...', flush=True)
            env.reset(['blue block', 'yellow bowl'])
            # Settle the scene with the same physics-step mimic control as during motion.
            for _ in range(120):
                env.step_sim_and_render()
            before = {name: env.get_obj_pos(name).tolist() for name in env.object_list}
            initial_distance = float(np.linalg.norm(np.array(before['blue block'])[:2] - np.array(before['yellow bowl'])[:2]))
            assert initial_distance > 0.10, 'Initial scene is already too close to the target'
            imageio.imwrite(out / 'before.png', env.get_camera_image())
            for _ in range(6):
                env.record_frame()
            print('Executing manual pick-and-place primitive ...', flush=True)
            # Same call made by LMP_wrapper.put_first_on_second, without an LLM.
            env.step({'pick': env.get_obj_pos('blue block')[:2].copy(),
                      'place': env.get_obj_pos('yellow bowl')[:2].copy()})
            for _ in range(480):
                env.step_sim_and_render()
            after = {name: env.get_obj_pos(name).tolist() for name in env.object_list}
            imageio.imwrite(out / 'after.png', env.get_camera_image())
            for _ in range(12):
                env.record_frame()
            distance = float(np.linalg.norm(np.array(after['blue block'])[:2] - np.array(after['yellow bowl'])[:2]))
            lifted = max(row[3] for row in env.trace) - before['blue block'][2]
            # Evaluate the official geometric predicate; also require actual lifting.
            predicate = bool(env.on_top_of('blue block', 'yellow bowl'))
            success = predicate and lifted > 0.05
            report.update(before=before, after=after, initial_xy_distance_m=initial_distance,
                          final_xy_distance_m=distance, max_lift_m=lifted,
                          official_on_top_of=predicate, success=success,
                          physics_steps=env.sim_step, frames=env.frames,
                          criterion='official on_top_of predicate AND max lift > 0.05 m')
    except Exception as exc:
        report.update(success=False, error=f'{type(exc).__name__}: {exc}')
        raise
    finally:
        report['wall_seconds'] = round(time.perf_counter() - start, 3)
        (out / 'result.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
        if env is not None:
            np.savetxt(out / 'block_trace.csv', np.asarray(env.trace), delimiter=',',
                       header='physics_step,x_m,y_m,z_m', comments='')
        if p.isConnected():
            p.disconnect()
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
    return 0 if success else 1


if __name__ == '__main__':
    raise SystemExit(main())
