# Code as Policies 项目协作约定

## 项目事实

- 默认用中文沟通；说明实际修改、验证结果和仍未验证的部分。
- 本仓库已完成离线手动抓放、文件程序回放，以及百炼 Qwen 真实生成程序驱动的单次/双色抓放：UR5e + Robotiq 2F85 + PyBullet。seed=0 的单物体、同色放置与交叉放置已通过独立目标检查；尚未完成多种子评估或论文实验复现。
- 官方快照固定在 `upstream.json` 中的 commit；`code_as_policies/` 保存原始 Notebook、README 和 Apache 2.0 许可证。
- 当前验证平台为 Windows，已验证 PowerShell 中的项目启动器和 CMD UTF-8 激活运行链路。独立环境为仓库内 `.conda-env/`，Python 3.10.22、NumPy 1.26.4、PyBullet 3.25。依赖以 `environment.yml` 为准。

## 复现进展交接（更新于 2026-10-28）

用户关注的主线是 **任务规划：自然语言任务 → 模型生成程序组织动作 → 机械臂执行与验证**。用户已提供百炼北京工作空间地址并配置本机密钥，真实模型生成与仿真已通过最小验收。模型版本为 `qwen3.7-plus-2026-05-26`；这是受限场景内的集成验证，不等于通用任务规划或完整论文复现。

| 内容 | 当前状态与边界 |
| --- | --- |
| 官方代码与资源 | 已按固定版本下载 `Interactive_Demo.ipynb`、`LMP Examples.ipynb`、README、LICENSE，以及 UR5e、Robotiq 夹爪和碗的资源。没有下载或运行其余实验 Notebook |
| 本地环境与项目整理 | 独立 Conda 环境、启动器、来源校验、标准库测试和 Git 仓库已准备；实际项目根目录为当前 `cap-study/` |
| 离线抓放 | 已重复验证 seed=0 的蓝色积木 → 黄色碗，产生视频、前后截图、状态轨迹和结果 JSON |
| 程序接口与离线回放 | `run_planner.py` 保存任务/提示词，解释受限程序并执行有序动作；单次和双色抓放均已通过 seed=0，程序来源明确为手写文件 |
| 自然语言与任务规划 | **真实调用与 seed=0 最小验收已通过**；单物体、双色同色与交叉放置由模型生成不同程序，独立目标均通过；文件模式仍仅回放手写程序 |
| 论文实验复现 | **尚未完成**；没有多任务、多种子成功率或对比实验结果 |

### 当前代码到底做了什么

- `run_offline.py` 是运行入口；它导入 `PickPlaceEnv`，用 `LocalEnv` 添加同步夹爪更新、录像和轨迹记录，再初始化场景、执行抓放并检查结果。
- `main()` 直接指定 `blue block` 和 `yellow bowl`，调用 `env.step({'pick': ..., 'place': ...})`。目标对象由代码写定，坐标来自仿真状态，不是视觉识别或模型推理。
- `src/upstream_env.py` 定义夹爪、场景和运动功能，供入口调用；单独运行该文件不会启动完整任务。接近、下降、夹紧、抬起、搬运、松开是预写的动作流程，不等于高层任务规划。
- `result.json` 的 `task` 是记录用的预设说明文字，不是送给模型的输入；`phase=offline_manual_control`、`model_api_used=false` 明确标识当前阶段。
- 当前后台仿真不弹出交互式机械臂窗口，过程保存在 `demo.mp4`。成功条件是本文件下文约定的几何关系与抬升检查，不代表通用抓取安全或完整任务规划正确性。
- 新入口 `run_planner.py` 将文件或模型文本通过受限 AST 解释器展开成动作数据，不使用 `exec/eval`；全部校验通过后，可用 `--execute` 启动可信仿真 worker。文件模式为 `phase=offline_program_replay`、`model_api_used=false`；模型模式为 `phase=model_program_generation`，状态区分请求尝试、收到响应、回复被接受、程序校验和物理执行。详细语法、限额、产物、命令见 `docs/planning.md`。
- 新场景 JSON 的 `goals` 是独立评估标签，不来自程序、不发给模型提示词。结果区分 `plan_validated`、`execution_success` 与 `task_success`；只编译或没有独立目标时不能判定任务成功。新入口默认唯一输出目录，拒绝覆盖已有目录。

### 最近一次已完成的验证

2026-10-28 完成真实百炼调用与仿真。4 次请求均 HTTP 200，返回模型均为 `qwen3.7-plus-2026-05-26`，合计用量 2097 tokens（含首次失败）。首次程序含不支持的 `and`，校验器拒绝且未启动仿真；随后只明确提示词中的禁用语法与嵌套条件写法，未扩大解释器白名单，未编辑模型回复。修订后 3 次生成、执行与独立目标均通过。这是调试后的最小验收，不能当作固定提示词的无偏成功率。

| 真实模型输出（均在 `outputs/` 下） | 结果 |
| --- | --- |
| `qwen_single_seed0_20261028_28dfd22b/` | 首次失败；`Unsupported syntax: BoolOp`，保留提示词/回复/程序和失败记录，未执行 |
| `qwen_single_seed0_20261028_036aa58e/` | 蓝块 → 黄碗，`success=true`、`task_success=true`；3241 步、99 帧 |
| `qwen_colors_seed0_20261028_1720aac4/` | 蓝块 → 蓝碗、红块 → 红碗，两个目标均通过；6573 步、182 帧 |
| `qwen_cross_seed0_20261028_8c9a6b8e/` | 蓝块 → 红碗、红块 → 蓝碗，两个目标均通过；6551 步、181 帧；最大抬升 0.167509 / 0.167334 m，最终水平距离 0.003435 / 0.002184 m |

同色与交叉任务的对象列表和初始位置完全相同；送入模型的差别只有自然语言指令，独立目标不进入提示词。三次成功运行的必需产物齐全、数值有限，视频均 12 fps，已抽查抓起、搬运与最终放置画面。单次与同色任务轨迹数值分别与先前文件回放逐项一致。

本次 37 项标准库 unittest 和 `build_env.py --check` 通过，增加了已观察到的布尔运算拒绝回归及嵌套条件正例。未新增依赖，未改物理控制、判据或 worker。密钥位于实际 Windows 用户的 `DASHSCOPE_API_KEY`；沙箱用户环境不可见，真实验证使用实际用户上下文读取，不输出密钥。一次性 QA 脚本及预览拼图在系统临时目录中生成并清理，实验原始产物与首次失败记录保留。

2026-10-27 已完成离线程序链路验证；当时 20 项标准库 unittest 和 `build_env.py --check` 全通过。新增依赖为零，官方快照和物理参数未改；`LocalEnv` 仅把默认蓝色积木轨迹采集扩展为可指定多个积木。

| 本轮输出（均在 `outputs/` 下） | 结果 |
| --- | --- |
| `planning_regression_seed0_20261027_a9d1/` | 原入口退出 0，`success=true`、`official_on_top_of=true`、`model_api_used=false`；抬升、距离、3241 步、99 帧均与 `repository_verified_seed0/` 一致，轨迹字节一致 |
| `planning_single_seed0_20261027_a9d1/` | 文件程序驱动蓝块 → 黄碗，退出 0，执行与独立目标均通过；轨迹数值逐项与基线一致（CSV 表头及数字格式不同），3241 步、99 帧 |
| `planning_colors_seed0_20261027_a9d1/` | 手写循环展开成蓝块 → 蓝碗、红块 → 红碗，退出 0，两个独立目标均通过；6573 步、182 帧。蓝/红块最大抬升分别 0.175467 / 0.167317 m，最终水平距离分别 0.002699 / 0.002222 m |

三次运行的五项必需产物齐全、数值有限，视频均为 12 fps；已抽查起始、积木抬升、搬运放置和最终画面。首次沙箱运行因 `outputs/` 的 Windows 写权限失败，未开始仿真；获取所需执行权限后在新目录完成验证。没有改动目录 ACL 或覆盖旧产物。以上仍只是 seed=0 的离线回放，未验证真实模型、多种子成功率或完整论文实验。

### 先前终端验证记录（保留历史边界）

2026-10-27 使用与本机 VS Code 配置相同的 CMD UTF-8 启动命令，正确初始化并激活项目环境，运行完整 seed=0 抓放。结果保存在 `outputs/terminal_utf8_check_20261027_bbf2a505/`，没有覆盖既有输出。

- 退出码为 0；`success=true`、`official_on_top_of=true`、`model_api_used=false`。
- 最大抬升 `0.16750884987413883 m`，最终水平距离 `0.010939221197891476 m`，物理步数 `3241`；视频 `99` 帧、`12 fps`，该次墙钟时间 `37.667 s`。
- 五项必需产物齐全且非空，轨迹和前后位置数值有限；指标与 `outputs/repository_verified_seed0/` 一致，`block_trace.csv` 字节一致。已核对视频帧数并抽查初始、抓起、搬运放置和结束画面。
- 同轮 6 项 `unittest` 全通过，`build_env.py --check` 通过。这些是已有验证记录，后续对话不要声称刚刚重新执行过。
- 该历史验证未覆盖用户在 VS Code 界面中新建终端后的实际反馈、其他操作系统、全新机器安装或多种子成功率；后续模型验证见上节。

### 下一阶段：固定配置的多任务、多种子评估

1. 已阅读原始 `Interactive_Demo.ipynb` 的 `LMP.__call__`、`LMPFGen`、`LMP_wrapper` 和 `setup_LMP`；对应关系记录在 `docs/planning.md`。本地没有直接调用这条旧 API 链路；递归辅助函数生成、会话记忆和完整几何 API 尚未实现，未知函数会被拒绝。
2. `src/model_provider.py` 已实现 `ProgramProvider.generate(messages) -> ProgramReply`，CLI 的 `--model-api` 与 `--program-file` 二选一。`config/model.json` 仅保存用户提供的北京工作空间地址及 `qwen3.7-plus-2026-05-26`；密钥从 `DASHSCOPE_API_KEY` 读取。地址/模型覆盖优先级为 CLI、环境变量、配置文件，不自动更换模型。官方旧 `openai.Completion.create` / `code-davinci-002` 保留原样。
3. 最小语言验收已完成。后续固定当前提示词与模型配置，预先定义任务和独立目标，跨多个 seed 记录全部成功、拒绝、规划失败和物理失败；不要边调提示词边汇报统一成功率。
4. `model_api_used=true` 表示尝试发出 HTTP 请求，含失败；需结合 `provider.response_received` / `reply_accepted`、`plan_validated` 与 `task_success` 判定各阶段。缺密钥时不发请求且为 false。继续保存无凭据配置、用量、回复和失败，不保存 HTTP 密钥、错误正文或思考内容；辅助函数仍不支持。

## 目录与修改入口

| 位置 | 职责与约束 |
| --- | --- |
| `run_offline.py` | 实验入口、同步夹爪控制、视频录制、状态轨迹及成功检查 |
| `run_planner.py`、`src/planning.py` | 程序来源协议、文件回放、提示词、受限 AST 解释与运行日志；不直接执行生成 Python |
| `src/model_provider.py`、`config/model.json` | 百炼 HTTPS 适配器与无密钥配置，环境变量读取密钥，拒绝重定向与不完整回复 |
| `src/plan_execution.py` | 只接收动作数据的可信仿真 worker，独立目标评估、多积木轨迹和失败记录 |
| `examples/planning/`、`docs/planning.md` | 手写离线样例、场景与独立目标、接口边界和操作说明 |
| `build_env.py` | 从官方 Notebook 的 7/8/9 号单元（从 0 计数）提取仿真代码，并应用本地适配 |
| `src/upstream_env.py` | **生成文件**：不要单独手改；修改 `build_env.py` 后重新生成 |
| `bootstrap_source.py`、`upstream.json` | 恢复固定版本的官方文件，下载前后核对 SHA-256 |
| `download_assets.py`、`assets/manifest.json` | 下载、校验、解压机械臂/夹爪/碗资源；清单进入 Git，资源本体不进入 Git |
| `tests/` | 无网络的标准库 unittest 测试，不依赖 pytest |
| `outputs/` | 本地实验产物，默认不进入 Git；已有结果不要随意覆盖或删除 |

## 环境与命令

所有命令均从仓库根目录执行。优先通过 Conda 运行，避免使用系统 Python 或其他科研环境。

```powershell
# 仅在缺少独立环境时创建；不要重建或覆盖已有环境。
conda env create --prefix ./.conda-env --file environment.yml

# Windows 启动器从 CONDA_EXE 或 PATH 查找 Conda，无须写死安装盘符。
.\run_python.cmd -m unittest discover -s tests -v
.\run_python.cmd build_env.py --check
.\run_python.cmd download_assets.py --check

# 修改提取规则之后生成，再检查。
.\run_python.cmd build_env.py
.\run_python.cmd build_env.py --check

# 实际物理回归：每次选择一个尚不存在的输出目录。
.\run_demo.cmd --seed 0 --output outputs/check_seed0_YYYYMMDD_HHMMSS

# 离线程序链路；不加 --execute 时只校验，默认新建唯一输出目录。
.\run_python.cmd run_planner.py --task "把各个积木放进对应颜色的碗" --scene examples/planning/match_colors.json --program-file examples/planning/match_colors.py --execute --seed 0

# 配置 DASHSCOPE_API_KEY 后调用百炼；先不执行物理仿真。
.\run_python.cmd run_planner.py --task "把蓝色积木放进黄色碗" --scene examples/planning/single.json --model-api
```

- 其他终端可使用 `conda run --prefix ./.conda-env --no-capture-output python ...`；目前未验证其他操作系统的仿真行为。
- 缺少资源时运行 `.\run_python.cmd download_assets.py`，该准备步骤需要联网。离线仿真和单元测试不需要 API Key 或网络。
- `conda-explicit-win-64.txt` 加 `requirements-pip.txt` 是 Windows 精确环境恢复入口；不要用包含本机 `file:///` 路径的 pip freeze 输出代替安装清单。

### 本机 VS Code 与终端问题的结论

- 在 VS Code 中打开整个 `cap-study/`，选择 `.conda-env/python.exe` 解释器，运行 `run_offline.py`。点击运行使用当前打开的文件；`Ctrl+F5` 是不调试运行，`F5` 是调试运行。脚本不依赖 Jupyter 扩展；原始 `.ipynb` 的查看/逐块执行是另一条使用路径。
- 当前实际环境 `.conda-env/` 保留且可用。旧 `.venv/` 和失败克隆残留已备份后清理，见 `docs/maintenance.md` 及仓库外 `_cleanup_backups/`；`.gitignore` 忽略环境仅表示不提交，不表示删除。
- 曾遇到三类不同的激活问题：PowerShell 禁止执行 `conda-hook.ps1`；未正确初始化的 CMD 提示先运行 `conda init`；CMD 用代码页 936 读取 UTF-8 激活脚本，导致中文路径乱码并报两次“系统找不到指定的路径”。最后一种已通过 936/65001 对照实验复现，改为 UTF-8 后环境路径匹配且激活成功。
- 已修改本机 `.vscode/settings.json`：普通终端默认配置与调试/任务终端均使用 CMD，并在启动时执行 `chcp 65001 >nul && call D:\Anaconda\condabin\conda_hook.bat`；普通终端配置名为 `CMD (Conda, UTF-8)`。配置生效需要结束旧终端、重新创建；不要只换成未初始化的普通 CMD 就宣称所有问题解决。
- `.vscode/` 是被 Git 忽略的本地配置，克隆到新机器不会携带；这里的 `D:\Anaconda` 仅对应本机安装位置，迁移时需核对。项目 `run_python.cmd` / `run_demo.cmd` 仍从 `CONDA_EXE` 或 `PATH` 查找 Conda，未写死安装盘符。
- “解释器已选中”与“终端成功激活环境”不同。VS Code 可在自动激活报错后直接调用所选 `python.exe`，因此本项目曾出现前面报错、后面仿真仍成功的情况；不能据此推断所有激活错误都无影响，也不能据此断言环境被删除。

## 科研与数据约束

- 不修改官方原始文件、原始实验输出或固定校验值来让测试通过。若任务明确要求升级上游或资源，记录来源和变更原因，再更新快照及清单。
- 不通过瞬移积木、硬改物体状态、放宽判据或跳过失败用例伪造成功。初始化场景时原有的关节复位逻辑不属于此限制。
- 当前成功条件：官方 `on_top_of` 为真，且积木相对初始高度的最大抬升超过 0.05 米。对碗的官方条件为水平距离小于 0.06 米且积木中心更高。
- 保存 seed、依赖版本、上游 commit 和失败记录。seed=0 的一次成功不能写成多任务成功率，也不能称为语言模型控制结果。
- 不默认执行原始 Notebook 的安装、API 调用或生成代码单元；其模型接口属于旧版示例。引入模型时须单独设计生成代码的执行边界。
- 新实验参数通过显式配置或 CLI 暴露。不要在清理或重构时顺带修改物理参数、控制增益、随机种子或判据。
- 保留许可证、来源信息和必要研究接口；没有当前调用不等于可以删掉官方 API。

## 完成标准

- 文档修改：核对目录、命令和描述与实际实现一致；不为纯文案新增测试。
- 本地 Python、下载或生成逻辑修改：相关 unittest 全通过，`build_env.py --check` 通过。
- 物理控制、生成环境或启动器修改：还需运行 seed=0 的完整离线演示；退出码为 0，`result.json` 中 `success` 和 `official_on_top_of` 为真，`model_api_used` 为假。
- 新程序执行器或多物体轨迹修改：还需按 `docs/planning.md` 运行单次与双色回放，检查独立目标、动作日志和视频；文件回放必须保持 `model_api_used=false`，没有独立目标不得报告任务成功。
- 确认 `demo.mp4`、`before.png`、`after.png`、`result.json`、`block_trace.csv` 已产生，数值为有限值；检查画面确实呈现抓起、搬运和落入碗中。
- 与已有基线比较抬升高度、最终水平距离、物理步数及状态轨迹。重构应保持行为；实验变更应解释差异，不能隐瞒失败。
- 无法执行的验证明确标为未验证，不把历史结果写成本次结果。

## Git 与清理

- 每次修改、调试、资料抓取或验证任务结束前，执行用户级 `project-cleanup` skill 做收尾；一次性辅助文件优先放在本任务独享的系统临时目录，用完删除。若 skill 在其他机器上不可用，仍遵守下一条清理边界。
- 只删除能确认由当前任务或本对话前序操作创建、临时用途已结束且没有依赖的一次性改写/抓取/验证脚本和中间产物；保留正式代码、回归测试、复现工具、必要文档、实验结果与失败记录。不能根据“由 AI 编写”、文件名或 Git 忽略状态判定可删，不通过回滚改动或清空输出目录来清理。
- 先查看 `git status`；不要撤销用户已有修改。是否提交、推送或发布遵循当前用户要求，不自动创建远端。
- 源码、文档、测试、依赖清单和资源校验清单进入 Git；环境、包缓存、下载资源、视频及本地配置保持忽略。
- `.env`、凭据、访问令牌不能进入代码或提交；需要配置示例时使用无密钥的占位值。
- 删除前核对调用和用途；对有价值或有歧义的旧文件先保留备份。Windows 递归删除必须先解析并确认路径位于本项目内。
- 改动目录、依赖或验证命令时同步更新本文件与 README；保持约定简短、可执行，不堆积聊天历史。
