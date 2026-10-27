# Code as Policies：从离线抓放开始

本仓库是 Code as Policies 官方仿真环境的 Windows 本地学习与复现入口。实际 Git 根目录是 `cap-study/`。AI 协作者先阅读 [AGENTS.md](AGENTS.md)，最近一次仓库整理见 [维护记录](docs/maintenance.md)。

## 目录结构

```text
cap-study/
├── AGENTS.md                 # 项目事实、操作约定与完成标准
├── run_demo.cmd              # Windows 离线演示入口
├── run_python.cmd            # 在本项目 Conda 环境中执行 Python
├── run_offline.py            # 抓放、录制、状态记录与成功检查
├── build_env.py              # 提取官方代码并应用本地适配
├── bootstrap_source.py       # 恢复并校验官方源码快照
├── download_assets.py        # 下载并校验仿真资源
├── src/upstream_env.py       # 生成的仿真环境，不直接编辑
├── code_as_policies/         # 原始 Notebook、README、许可证
├── assets/manifest.json      # 资源来源与固定 SHA-256
├── tests/                    # 无网络的回归检查
├── docs/                     # 维护说明
├── environment.yml          # Conda 环境定义
├── conda-explicit-win-64.txt  # Windows Conda 包精确快照
├── requirements-pip.txt      # Conda 之外的 pip 依赖
└── upstream.json             # 官方版本与文件校验值
```

`.conda-env/`、`.conda-pkgs/`、下载的资源和 `outputs/` 仅保存在本机，不进入 Git。

## 首次安装或迁移到新电脑

准备 Conda 和 Git，在终端进入克隆或解压后的仓库根目录。当前已验证 Windows，不要求 GPU，也不需要模型 API Key。

```powershell
# 如果已经存在可用的 .conda-env，就跳过这一行。
conda env create --prefix ./.conda-env --file environment.yml

# 首次准备需要联网；原始 Notebook 已随仓库保存。
.\run_python.cmd download_assets.py
.\run_python.cmd build_env.py --check
.\run_demo.cmd --seed 0 --output outputs/first_run_seed0
```

`run_python.cmd` 自动从 `CONDA_EXE` 或 `PATH` 查找 Conda。找不到时，在 Anaconda Prompt 中执行，或将 `CONDA_EXE` 设为本机 `conda.exe` 的完整路径。不要将 Conda 安装盘符写入源码。其他终端可使用：

```text
conda run --prefix ./.conda-env --no-capture-output python run_offline.py --seed 0 --output outputs/first_run_seed0
```

若需要恢复本项目记录的 Windows Conda 包版本，可用以下两步**替代** `conda env create`：

```powershell
conda create --prefix ./.conda-env --file conda-explicit-win-64.txt
.\run_python.cmd -m pip install -r requirements-pip.txt
```

Git 仓库不包含资源压缩包和 Conda 环境，因此新电脑仍需联网安装和下载；准备完成后，演示运行不联网。若下载资源的大小或 SHA-256 不匹配，程序会停止，不自动改写固定清单。

## 当前进度

第一阶段已运行成功：使用官方 UR5e 机械臂、Robotiq 夹爪和 PyBullet 场景，将蓝色积木抓起并放入黄色碗中。此阶段不调用语言模型，不需要 API Key，运行时不需要联网。它验证了机械臂动作和仿真环境，还不代表完成了论文的自然语言控制或全部实验复现。

实测结果（seed=0）：积木最高抬升 0.1675 米，放置后与碗中心的水平距离为 0.01094 米；官方 `on_top_of` 判定和额外的抬升检查均通过。用时约 40 秒，视频 99 帧、12 fps，时长约 8.25 秒。视频和最终截图也已检查。

## 现在怎么操作

1. 打开 `outputs/offline_seed0/demo.mp4`，先看抓取、搬运、放置的完整过程。
2. 在 PowerShell 中执行下列两行，亲自重复一次：

```powershell
cd 'D:\任务规划：Code as Policies\cap-study'
.\run_demo.cmd
```

正常运行约需几十秒。程序在后台计算并录制视频，不弹出交互式仿真窗口。结束时输出 `"success": true`，并更新 `outputs/offline_seed0/` 中的结果。重复执行会覆盖该目录的上次输出。也可双击 `run_demo.cmd`，结束后到输出目录查看视频。

如需保留不同初始场景的结果：

```powershell
.\run_demo.cmd --seed 1 --output outputs/offline_seed1
```

目前验证的是 seed=0 的单个场景，不能据此推断其他场景的成功率。程序返回码为 0 表示检查通过；返回码非 0 时先查看终端和 `result.json`。它不会通过移动物体坐标来伪造抓取。

## 你今天先读懂这三个位置

| 位置 | 学习重点 |
| --- | --- |
| `run_offline.py` 的 `main()` | 创建环境 → 初始化物体 → 取得目标位置 → 发出抓放动作 → 检查结果 |
| `src/upstream_env.py` 的 `PickPlaceEnv.step()` | 抓放动作怎样拆成接近、下降、夹紧、抬起、搬运、松开 |
| `src/upstream_env.py` 的 `movep()` 与 `servoj()` | 末端目标位置怎样通过逆运动学和关节位置控制变成机械臂运动 |

核心调用是：

```python
env.step({
    'pick': env.get_obj_pos('blue block')[:2].copy(),
    'place': env.get_obj_pos('yellow bowl')[:2].copy(),
})
```

这里的坐标来自仿真环境的物体状态，不是通过摄像头识别得到的。当前动作由程序明确指定；之后再让语言模型根据自然语言生成这类调用。

## 怎么用 Codex 学这个项目

在 Codex 中打开本项目目录，每次完成一个可验证的小任务。可以按顺序发送：

1. “请阅读 run_offline.py，用控制工程的语言讲清楚 main 到 step、movep、servoj 的调用关系。先解释，不修改代码。”
2. “请说明抓放过程中的目标量、反馈量、控制量分别是什么，并带我定位实际使用这些量的代码。”
3. “请检查官方 Interactive_Demo.ipynb 中的 LMP、LMPFGen 和 LMP_wrapper，画出自然语言到动作的调用关系，再列出接入当前模型 API 所需的最小改动。”

读代码时重点确认：输入是什么、输出是什么、状态从哪里来、执行什么动作、怎样判定成功。让 Codex 指到实际函数，并用运行结果验证解释。

## 后续复现路线

| 阶段 | 验收标准 | 当前状态 |
| --- | --- | --- |
| 1. 离线物理抓放 | 有视频、有状态轨迹、抓起并落到碗中 | 已通过 seed=0 |
| 2. 文本生成控制代码 | 输入一条指令，保存模型返回的代码并检查调用 | 待接入模型 API |
| 3. 语言控制仿真 | 模型生成的调用驱动同一个环境，并检查最终状态 | 待完成 |
| 4. 实验复现 | 多任务、多随机种子、固定提示词和模型配置，统计成功率 | 待完成 |

官方旧 Notebook 使用 `code-davinci-002` 和旧版 `openai.Completion.create`，后续需要迁移模型接口；不要照着旧 Notebook 一次性执行所有单元。当前项目没有安装 OpenAI SDK，也没有读取、保存或调用 API Key。将来配置密钥使用本机环境变量，不写进源码或聊天记录。

## 来源与本地调整

- 官方主页：https://code-as-policies.github.io/
- 官方源码：https://github.com/google-research/google-research/tree/master/code_as_policies
- 固定版本：`0f585ccae259c32a877edfd48362c18a0010545f`；下载地址和校验值保存在 `upstream.json`。
- `code_as_policies/` 保存原始 Notebook、README 和许可证；`assets/manifest.json` 记录官方演示所用资源的下载来源与校验值。
- `build_env.py` 从原始 Notebook 的第 7、8、9 号单元（从 0 计数）提取仿真代码，生成 `src/upstream_env.py`。
- 本地适配：夹爪联动改为在 `LocalEnv` 的物理步中同步更新，移除停用线程及其重置逻辑；移除未被调用且依赖不存在的 `moving()` 的 `check_grasp()`；修正相机方法中的全局变量引用；使用 CPU 渲染；将 PyBullet 内置数据路径转成相对路径以支持当前 Windows 中文目录。
- `run_offline.py` 增加固定随机种子、相机视角、视频记录、步数上限和结果检查。原始 Notebook 保持原样。

这些改动意味着它是基于官方环境的本地适配版本，并非对论文所有运行条件的逐项复刻。项目内保留原始 Apache 2.0 许可证。

## 环境与输出

独立环境位于 `.conda-env/`，启动器会定位本机 Conda，不修改其他科研环境。`environment.yml` 记录直接依赖；`conda-explicit-win-64.txt` 记录 Windows Conda 包；`requirements-pip.txt` 记录额外 pip 依赖。旧的 `pip-freeze.txt` 含构建机器的 `file:///` 路径，已替换为可安装的版本清单。

| 输出 | 内容 |
| --- | --- |
| `demo.mp4` | 抓放视频 |
| `before.png`、`after.png` | 执行前后截图 |
| `result.json` | 版本、初末位置、抬升高度、成功判定 |
| `block_trace.csv` | 每个物理步的积木位置，单位为米 |

当前成功判据是官方 `on_top_of` 几何条件（水平距离小于 0.06 米且积木中心高于碗中心）与实际抬升超过 0.05 米同时成立，再检查截图。这是单任务验证，并非通用的接触或碰撞安全判定。

## 开发与验证

```powershell
.\run_python.cmd -m unittest discover -s tests -v
.\run_python.cmd build_env.py --check
.\run_python.cmd download_assets.py --check
```

上述测试不联网；资源检查需要已下载的压缩包和已解压的 URDF。单元测试覆盖官方快照、生成文件一致性、资源篡改和解压路径越界。修改 `build_env.py` 后运行 `.\run_python.cmd build_env.py`，同时保留生成文件进入 Git。

修改机械臂控制、生成环境或启动器后，再运行完整演示并选择新的输出目录。检查退出码、`result.json`、状态轨迹以及视频，不能只凭单元测试通过宣称物理抓放成功。

需要恢复官方文件时运行 `.\run_python.cmd bootstrap_source.py`：现有文件通过哈希校验时不联网，否则从 `upstream.json` 中的固定地址恢复。不要直接执行原始 Notebook 的全部单元。

## Git 使用与协作

仓库只管理源码、文档、测试、依赖和来源清单；`.gitignore` 排除环境、资源本体、产物和本地密钥。`.gitattributes` 为脚本统一换行，并保留官方快照原始字节。

日常先运行 `git status`，完成修改和验证后再按需要 `git add` / `git commit`。远端地址由项目维护者设置；本地整理不会自动发布到 GitHub。

[AGENTS.md](AGENTS.md) 用于保存项目协作约定，内容基于本仓库实际命令。其加载方式参考 [OpenAI 官方说明](https://developers.openai.com/codex/guides/agents-md/)。建议在 Codex 中直接打开 `cap-study/`；上层工作区的 `AGENTS.md` 也提供了进入本项目的指引。
