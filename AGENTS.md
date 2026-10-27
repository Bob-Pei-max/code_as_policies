# Code as Policies 项目协作约定

## 项目事实

- 默认用中文沟通；说明实际修改、验证结果和仍未验证的部分。
- 本仓库当前仅完成离线手动抓放：UR5e + Robotiq 2F85 + PyBullet，将蓝色积木放入黄色碗。还没有接入语言模型，也没有完成论文实验复现。
- 官方快照固定在 `upstream.json` 中的 commit；`code_as_policies/` 保存原始 Notebook、README 和 Apache 2.0 许可证。
- 当前验证平台为 Windows / PowerShell，独立环境为仓库内 `.conda-env/`，Python 3.10.22、NumPy 1.26.4、PyBullet 3.25。依赖以 `environment.yml` 为准。

## 目录与修改入口

| 位置 | 职责与约束 |
| --- | --- |
| `run_offline.py` | 实验入口、同步夹爪控制、视频录制、状态轨迹及成功检查 |
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
```

- 其他终端可使用 `conda run --prefix ./.conda-env --no-capture-output python ...`；目前未验证其他操作系统的仿真行为。
- 缺少资源时运行 `.\run_python.cmd download_assets.py`，该准备步骤需要联网。离线仿真和单元测试不需要 API Key 或网络。
- `conda-explicit-win-64.txt` 加 `requirements-pip.txt` 是 Windows 精确环境恢复入口；不要用包含本机 `file:///` 路径的 pip freeze 输出代替安装清单。

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
- 确认 `demo.mp4`、`before.png`、`after.png`、`result.json`、`block_trace.csv` 已产生，数值为有限值；检查画面确实呈现抓起、搬运和落入碗中。
- 与已有基线比较抬升高度、最终水平距离、物理步数及状态轨迹。重构应保持行为；实验变更应解释差异，不能隐瞒失败。
- 无法执行的验证明确标为未验证，不把历史结果写成本次结果。

## Git 与清理

- 先查看 `git status`；不要撤销用户已有修改。是否提交、推送或发布遵循当前用户要求，不自动创建远端。
- 源码、文档、测试、依赖清单和资源校验清单进入 Git；环境、包缓存、下载资源、视频及本地配置保持忽略。
- `.env`、凭据、访问令牌不能进入代码或提交；需要配置示例时使用无密钥的占位值。
- 删除前核对调用和用途；对有价值或有歧义的旧文件先保留备份。Windows 递归删除必须先解析并确认路径位于本项目内。
- 改动目录、依赖或验证命令时同步更新本文件与 README；保持约定简短、可执行，不堆积聊天历史。
