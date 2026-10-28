# 任务规划接口、模型配置与验证

本阶段将“程序 → 已校验的动作序列 → 同一仿真环境 → 独立目标检查”连起来。`FileProgramProvider` 读取手写文件，不会理解自然语言；`QwenProgramProvider` 将任务与对象名交给百炼 Chat Completions API，读取模型生成的程序。两个入口共用受限校验器和仿真 worker。2026-10-28 已通过真实模型驱动的 seed=0 单物体、双色同色及交叉放置验收；尚未完成多种子评估。

## 官方链路与本地实现

已阅读固定快照 `code_as_policies/Interactive_Demo.ipynb` 的以下单元（从 0 计数）：

| 官方位置 | 作用 | 本地阶段的对应关系 |
| --- | --- | --- |
| cell 5，`LMP.build_prompt` / `LMP.__call__` | 拼接提示词、调用旧 Completion API、执行生成程序 | `build_messages` 保存任务与对象名；`ProgramProvider.generate` 支持文件回放或百炼模型调用，程序经受限校验后转换为动作 |
| cell 5，`LMPFGen` | 发现未定义函数，递归生成并执行辅助函数 | 本阶段不支持；未知函数拒绝，`helper_functions.json` 明确记录未启用 |
| cell 12，`LMP_wrapper.put_first_on_second` | 将对象名解析为环境坐标，再调用 `env.step` | 受限程序编译成 `Action(pick, place)`；可信执行器在动作开始时读取对象坐标 |
| cell 23，`setup_LMP` | 绑定环境 API，组装高层和低层 LMP | 新入口组装提示词、程序来源、校验器、仿真 worker 和评估器 |

官方的 `exec_safe` 是短语检查后调用 Python `exec`，不是充分的隔离。本地不调用它，也不改动官方文件。新适配器使用 Python 标准库 HTTPS，无新增依赖；接口参数依据 [百炼 Chat Completions 文档](https://help.aliyun.com/zh/model-studio/qwen-api-via-openai-chat-completions)（2026-10-28 核对）。替换旧 `code-davinci-002` 并使用受限语法，属于本地集成，不能作为原论文运行条件的逐项复刻。

## 文件回放

从 `cap-study/` 根目录执行。以下命令均不联网、不需要密钥；默认创建唯一输出目录，显式 `--output` 指向已存在目录时会拒绝覆盖。

仅检查并展开程序，不启动 PyBullet：

```powershell
.\run_python.cmd run_planner.py --task "把蓝色积木放进黄色碗" --scene examples/planning/single.json --program-file examples/planning/single.py
```

执行单物体抓放并生成视频：

```powershell
.\run_python.cmd run_planner.py --task "把蓝色积木放进黄色碗" --scene examples/planning/single.json --program-file examples/planning/single.py --execute --seed 0
```

执行双色任务，程序中的循环按场景对象顺序展开：

```powershell
.\run_python.cmd run_planner.py --task "把各个积木放进对应颜色的碗" --scene examples/planning/match_colors.json --program-file examples/planning/match_colors.py --execute --seed 0
```

`examples/planning/*.py` 是手写的测试输入，不能当成模型输出或自然语言任务成功案例。改变动作时编辑或另建程序文件；改变任务评估目标时另建场景 JSON。场景的 `goals` 是人工提供的独立评估标签，不从生成程序反推，也不放入模型提示词。

## 百炼模型调用

`config/model.json` 已配置用户提供的北京工作空间地址与快照 `qwen3.7-plus-2026-05-26`，其中只有 `base_url` 和 `model`，不接受密钥字段。地址按 [官方工作空间域名规则](https://help.aliyun.com/zh/model-studio/compatibility-of-openai-with-dashscope) 拼接 `/chat/completions`；提供完整接口地址也可识别。

先在 Windows“编辑账户的环境变量”中新增用户变量 `DASHSCOPE_API_KEY`，值填写该北京地域的 API Key。不要写入源码、命令行参数或聊天。设置后打开新终端；已经运行的终端或 Codex 不会自动继承新值。也可以在当前 PowerShell 中只读取已保存的用户变量，不输出内容：

```powershell
$env:DASHSCOPE_API_KEY = [Environment]::GetEnvironmentVariable('DASHSCOPE_API_KEY', 'User')
```

API Key 的获取和地域匹配参见 [百炼密钥配置文档](https://help.aliyun.com/zh/model-studio/get-api-key)。以下命令会调用付费 API；不加 `--execute` 时只生成并校验程序：

```powershell
.\run_python.cmd run_planner.py --task "把蓝色积木放进黄色碗" --scene examples/planning/single.json --model-api

# 真实模型生成双色任务程序，通过校验后才启动仿真。
.\run_python.cmd run_planner.py --task "把各个积木放进对应颜色的碗" --scene examples/planning/match_colors.json --model-api --execute --seed 0

# 相同物体和初始随机种子，改变指令及独立目标标签。
.\run_python.cmd run_planner.py --task "把蓝色积木放进红色碗，把红色积木放进蓝色碗" --scene examples/planning/cross_colors.json --model-api --execute --seed 0
```

`--model-api` 与 `--program-file` 必须二选一。文件模式不会读取模型配置或密钥，也不会联网。

| 设置 | 默认与覆盖方式 |
| --- | --- |
| 地址、模型 | CLI `--base-url` / `--model` 优先，其次环境变量 `CAP_MODEL_BASE_URL` / `CAP_MODEL_NAME`，最后 `config/model.json`；`--model-config` 可指定另一份同结构 JSON |
| 密钥 | 仅从 `DASHSCOPE_API_KEY` 读取；`--api-key-env` 可改变量名，不能传密钥本身 |
| 输出预算 | `--max-completion-tokens 4096`，包含思考与回答，允许 1–32768；截断回复不会编译 |
| 思考模式 | `--thinking auto` 沿用服务默认，不发送开关；`enabled` / `disabled` 显式发送 `enable_thinking`；其他模型需先确认参数兼容性 |
| 网络等待 | `--request-timeout 120`，允许大于 0 且不超过 300 秒，是 socket 等待超时，不是总墙钟上限 |
| 采样与重试 | 固定 `temperature=0`、非流式；每次运行仅一次请求，无自动重试，不保证模型结果完全确定 |

地址须为 HTTPS 443 端口，不带用户密码、查询参数或片段；HTTP 重定向会被拒绝。回复最多 1 MiB，程序仍最多 16 KiB。只有正常结束的单条助手文本可进入编译；截断、拒绝、工具调用、空回复或非法程序均终止，不启动 worker。

日志保存请求/返回模型标识、请求 ID、用量、结束原因、配置与状态，不保存 Authorization、密钥、服务端错误正文或思考内容。可用的失败文本会留在 `response.txt`，不会执行；没有自动换模型、修改指令或用手写程序顶替失败回复。鉴权或限流失败可查看 `result.json` 中的 HTTP 状态与提示。

## 程序边界

支持如下闭合语法集：字符串、列表/元组、单变量赋值、有限 `for`、`if/else`、`==` / `!=`、字符串 `+`；查询接口为 `get_obj_names()`、`get_color(name)`、`is_block(name)`、`is_bowl(name)`。唯一动作是 `put_first_on_second(block_name, bowl_name)`，且只能作为独立语句。

```python
for obj in get_obj_names():
    if is_block(obj):
        put_first_on_second(obj, get_color(obj) + ' bowl')
```

- AST 类型白名单检查覆盖整个程序，包括未进入的分支。导入、属性访问、下标、任意函数调用、函数定义、推导式、`while`、`and/or/not` 和直接坐标均不支持；组合条件可使用嵌套 `if`。
- 解析器只解释受限语法并输出不可变动作数据，不使用 Python `exec`、`eval` 或动态导入。全部动作生成并检查通过之后才启动仿真；晚出现的非法语句不会导致前半段动作先执行。
- 最多 16 KiB 源码、1000 个 AST 节点、24 层 AST 深度、2000 次解释操作、32 个集合元素、128 字符的字符串。每个积木最多抓取一次，动作数上限通过 `--max-actions` 指定，范围 1–4。
- 当前场景只接受官方颜色名的 2–4 个唯一对象，至少一个积木和一个碗；只支持积木到碗，不支持堆叠、重抓、任意轨迹或递归函数生成。
- 物理 worker 只接收动作数据，沿用官方运动流程与同步夹爪控制。默认最多 48000 个物理步；`--max-steps` 可调低。默认进程墙钟上限 180 秒，通过 `--simulation-timeout` 调整，包含上游随机布置场景的耗时。超时会保留已有文件，但视频或轨迹可能不完整。

这是受限语言的执行边界，不是通用 Python 沙箱，也不提供真实机器人安全保证。后续扩大 API 或语法时，需要重新审查权限与资源上限。

## 产物与判定

| 产物 | 内容 |
| --- | --- |
| `scene.json`、`prompt.json` | 场景/独立目标，以及只包含任务、对象名和接口说明的提示词 |
| `response.txt`、`program.py`、`helper_functions.json` | 文件或模型回复文本、去除可选代码围栏后的程序、不支持函数生成的声明；失败时可能只有部分产物 |
| `plan.json` | 全部校验通过后展开的有序动作列表 |
| `result.json` | 阶段、任务、seed、来源 commit、程序哈希、provider 信息、限额、状态与错误 |
| `execution_request.json`、`simulation.log`、`execution_result.json` | 可信执行器的输入、进程日志、物理结果和依赖版本 |
| `initial_state.json`、`actions.jsonl` | 稳定后的初始位置、每个动作开始/结束与阶段测量；异常中断可能只有开始记录 |
| `demo.mp4`、`before.png`、`after.png`、`block_trace.csv` | 完整执行时的视频、前后图像、所有积木的每步 XYZ 轨迹 |

`phase=offline_program_replay`、`model_api_used=false` 表示文件回放。只编译时 `plan_validated=true` 不等于物理或任务成功，`success=null`。运行后的 `execution_success` 依据各动作的实际抬升与官方几何判据，结束时再检查早先目标是否被后续动作破坏。

模型模式使用 `phase=model_program_generation`。`model_api_used` 表示已经尝试发出真实 HTTP 请求（含鉴权失败/连接超时），不表示模型成功回复；缺少密钥或配置错误时仍为 `false`。另看 `provider.response_received`、`provider.reply_accepted`、`plan_validated` 和 `execution_completed`，依次区分收到 HTTP 响应、收到可编译文本、通过程序校验和完成物理执行。单元测试在临时目录中模拟 HTTP，不属于真实调用证据。

`task_success` 依据独立 `goals` 的最终关系和相对初始位置的最大抬升超过 0.05 米；对碗的官方关系仍为水平距离小于 0.06 米且积木中心更高。所有执行检查通过时，`success` 取 `task_success`；执行失败则为 `false`。未提供目标时 `task_success=null`，不能声称自然语言任务成功。某步抓放失败后停止后续动作并保留记录。

退出码 0：编译通过，或者执行检查通过且没有失败的独立目标；无目标时仍可能是 0，所以应同时查看 `task_success`。退出码非 0：程序/配置错误、物理动作或目标检查失败、超时或 worker 异常。

## 验证状态与后续验收

2026-10-28：37 项无网络 unittest 与 `build_env.py --check` 通过，覆盖配置、请求格式、敏感字段、缺失密钥、HTTP 错误/重定向/超时、非法回复拦截，以及实际遇到的布尔运算拒绝和嵌套条件正例。物理控制与 worker 未改。

真实服务返回的模型均为 `qwen3.7-plus-2026-05-26`。共 4 次调用，首次程序使用 `and`，在仿真启动前被拒绝；随后明确提示词限制，3 次任务均通过。全部请求和失败保留，合计 2097 tokens。下表输出位于 `outputs/`，均含真实回复与使用的提示词。

| 输出目录 | 任务与结果 | 物理步 / 视频帧 |
| --- | --- | --- |
| `qwen_single_seed0_20261028_28dfd22b` | 首次 `BoolOp` 语法失败，未启动仿真 | 无 |
| `qwen_single_seed0_20261028_036aa58e` | 蓝块 → 黄碗，执行与目标通过 | 3241 / 99 |
| `qwen_colors_seed0_20261028_1720aac4` | 蓝块 → 蓝碗、红块 → 红碗，两个目标通过 | 6573 / 182 |
| `qwen_cross_seed0_20261028_8c9a6b8e` | 蓝块 → 红碗、红块 → 蓝碗，两个目标通过 | 6551 / 181 |

同色和交叉任务的对象、初始位置、模型与系统提示词相同，模型输入中的变化仅为任务文字；事先给定的 `goals` 仅用于评估。模型返回了不同的目标动作。单物体与同色运行的轨迹数值分别与先前文件回放完全一致；三次成功运行的必需产物齐全、数值有限，视频均为 12 fps，并已抽查抓取、抬升、搬运与最终放置画面。

这属于开发阶段最小验收，含提示词修订，不是固定条件的统计成功率。下一步应固定模型与提示词，预先定义更多任务和种子，记录所有规划、语法及物理失败。递归辅助函数、完整几何 API、会话记忆仍未实现；不能称为完整 CaP 或论文实验复现。
