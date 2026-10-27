# 仓库整理记录：2026-10-27

## 整理范围

- 保留原有 `cap-study/.git` 和 `main` 分支，未建立嵌套仓库。
- 按用户要求暂不提交，不设置提交身份或远端，不推送。
- 新增项目 `AGENTS.md`，以及上层工作区入口说明；补全 README 中的安装、目录、验证和 Git 使用方式。
- 新增 `.gitattributes`，完善 `.gitignore`：保留资源校验清单，排除环境、下载资源、视频、缓存和本地密钥。
- 启动器从 `CONDA_EXE` 或 `PATH` 定位 Conda，移除固定的 `D:\Anaconda` 安装路径；修正 README 中过期的工作目录。
- 将包含构建机器 `file:///` 路径的 `pip-freeze.txt` 替换为 `requirements-pip.txt`。NumPy、PyBullet 等仍由 Conda 精确清单负责。
- 源码和资源恢复脚本按既有 SHA-256 校验，不再用下载结果覆盖固定校验清单；新增 `build_env.py --check` 和 `download_assets.py --check`。

## 删除与保留

| 内容 | 处理与依据 |
| --- | --- |
| `.venv/` | 已备份后删除；仅有 pip/setuptools 等安装工具，运行入口使用 `.conda-env/` |
| `.upstream-clone-incomplete/` | 已备份后删除；仅含官方固定 commit 的失败克隆残留，必要官方文件已保存在仓库 |
| 夹爪后台线程、线程清理和专用字段 | 从生成环境中删除；原有本地适配已停止启动线程，运行入口使用同步夹爪控制 |
| `Robotiq2F85.check_grasp()` | 从生成环境中删除；无调用，且依赖不存在的 `moving()`；原始 Notebook 保留原样 |
| 相关 `threading` / `sleep` 导入 | 随停用代码一起删除 |
| `.conda-env/`、`.conda-pkgs/` | 保留现用环境与 Conda 缓存，均不进入 Git |
| 官方 Notebook、LICENSE、资源、原始实验结果 | 保留；源文件与资源压缩包通过固定哈希校验 |

删除在本地适配生成器中实现，`src/upstream_env.py` 重新生成。其余官方研究接口未按“暂时没调用”批量裁剪。

清理前备份位于仓库旁的 `_cleanup_backups/cap-study-before-cleanup-20261027-160036.zip`，共 1,992 个文件，9,057,028 字节。包含原始源码、小型配置、待删除目录、资源清单及原 `outputs/offline_seed0/`。不包含现用 Conda 环境、Conda 包缓存和大型资源本体。

备份 SHA-256：`30d42cf20468dda429ed485d421b14c36ec71b39f824315c174f19c1e5a8580e`。备份保留在本机，不纳入源码仓库或发布包。

## 实际验证

平台：Windows；Python 3.10.22，NumPy 1.26.4，PyBullet 3.25。验证未调用模型 API。

- `run_python.cmd -m unittest discover -s tests -v`：6 项通过。
- `run_python.cmd build_env.py --check`：通过。
- `run_python.cmd download_assets.py --check`：三个资源压缩包哈希和必需 URDF 均通过。
- 清理前后各运行一次 seed=0 的完整离线抓放，分别保存到 `outputs/repository_baseline_seed0/` 和 `outputs/repository_verified_seed0/`，均返回 0。

| 指标 | 清理前 | 清理后 |
| --- | ---: | ---: |
| `success` / `official_on_top_of` | true / true | true / true |
| 最大抬升（米） | 0.16750884987413883 | 0.16750884987413883 |
| 最终水平距离（米） | 0.010939221197891476 | 0.010939221197891476 |
| 物理步数 | 3,241 | 3,241 |
| 视频帧数 / 帧率 | 99 / 12 fps | 99 / 12 fps |
| 墙钟用时（秒） | 38.871 | 38.570 |

除墙钟时间外，两个 `result.json` 内容一致；`block_trace.csv`、`demo.mp4`、`before.png`、`after.png` 的 SHA-256 分别完全一致。已确认轨迹数值有限，视频全部 99 帧可解码，并检查初始、抓起、放置和结束画面。

这次验证只证明既有 seed=0 场景在整理前后保持一致，不代表多种子成功率。未在新机器上重新下载依赖或资源，也未验证其他操作系统；它们仍需按 README 的安装流程准备。
