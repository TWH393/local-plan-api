# Local Plan API

**中文** | [English](README.en.md)

免费、开源发布用的 Windows 本地 ChatGPT 套餐兼容网关。用户在自己的电脑上完成官方 OAuth 登录，应用请求通过本机接口使用该用户获准的套餐能力。

当前版本 0.3.8：ChatGPT 授权由本工具独立管理。默认端口 `24664`，凭据使用 Windows DPAPI 绑定当前 Windows 用户加密，存放在 `%LOCALAPPDATA%/LocalPlanAPI/`。

## 当前状态

本项目处于早期测试阶段，优先支持 Windows 本机使用。界面支持中文、English、模型与思考深度设置、用量记录及开机自启动。

| 应用 | 当前进展 |
|---|---|
| Continue / AnythingLLM / Open WebUI | 已有用户环境连接验证，一键配置仍可能受应用版本影响 |
| Cline Desktop / Cline VS Code | 已提供独立配置与撤销，部分客户端会话曾出现密钥加载问题，仍需继续实测 |
| DeepSeek Harness | 已完成桌面 profile 配置写入，实际对话待用户验证 |
| AstrBot | 支持本机配置写入、检查和撤销，实际聊天待用户验证 |
| Cherry Studio | 提供下载入口和应用内登录说明 |

“检查连接”只检查本地配置、密钥和模型目录，不会发送推理请求，也不代表所有工具功能都已验证。

## 开始使用

1. 从 GitHub 下载源码后：安装 Windows 版 Python 3.11+（包含 Python Launcher），双击 `start.cmd`，首次启动会创建虚拟环境并安装依赖。仓库不包含已编译的 EXE。若已自行构建便携版，可双击 `open.cmd` 启动 `dist/LocalPlanAPI/LocalPlanAPI.exe`。
2. 本地页面点击 **Continue with ChatGPT**，在 OpenAI 官方网页登录、授权。
3. 点击“一键连接 Continue”，自动检测本机标准安装、备份并填写配置；不必手动复制地址、密钥或编辑 YAML。
4. 首次连接后，在 Continue 模型列表选择 **ChatGPT · Local Plan API**。未出现时重新加载 VS Code 窗口。之后在桥页面切换默认模型即可，不用改 Continue 配置。

首次连接会沿用可用的已选模型；没有可用默认值时优先选账号目录中的 GPT-6 Luna，否则选目录首项。需要先安装 Continue 扩展并完成套餐授权。仅适配本机标准 VS Code/Insiders/VSCodium 的 Continue，不自动配置远程 SSH、WSL 或自定义扩展目录。

连接会保留其他模型、规则和注释，原文件备份使用 DPAPI 加密。重复点击不会反复添加。点击“撤销连接”只移除本工具添加的模型：如果文件没被其他程序修改则恢复原文；如果有后续修改则仅移除本工具的连接项。被手动改过的连接项不会强制覆盖或删除。

Continue 使用仅限兼容入口的专用本地密钥，撤销时停用该密钥，不影响通用 API 密钥。普通模型请求使用 `local-plan-default` 别名，由桥映射至当前默认模型。不会向模型发送“检查连接”请求；检查仅验证配置、密钥和官方模型目录，不代表真实推理已经成功。

Windows 默认作为独立后台进程运行，打开新项目或关闭 VS Code 终端不影响它。重复打开同版本只打开管理页。打开新版时会尝试请旧版正常退出；旧版有正在处理的请求时不会强制终止。

停止：页面底部点“停止本地服务”。需要终端调试时使用 `python run.py --foreground`。关闭网页不会停止服务。便携包需要完整文件夹，不能只复制 EXE。

默认 Base URL：`http://127.0.0.1:24664/v1`。密钥由本工具生成，和 OpenAI OAuth 凭据不同。不要在其他应用中粘贴 OAuth token。

也可用 `python run.py --port 24664 --no-browser` 启动。再次启动同端口不会创建第二个实例。诊断日志位于 `%LOCALAPPDATA%/LocalPlanAPI/runtime.log` 和 `error.log`，不记录 OAuth 回调访问日志。

## 兼容范围

| 接口/能力 | 首版行为 |
|---|---|
| GET /v1/models | 当前账号官方目录，保留真实模型 ID |
| POST /v1/responses | 非流式结果或真实 SSE 转发 |
| POST /v1/chat/completions | 消息、图片 URL/data URL、function tools、工具结果、结构化输出转换 |
| Chat 流式 | 文本增量、function 参数增量、finish_reason、可选 usage |
| 请求取消 | 客户端断开时关闭上游连接 |
| 未完成/额度错误 | 返回明确错误；流开始后的错误用 SSE error，绝不发成功 finish_reason |
| 使用量 | 记录调用时间、模型、状态、上游报告的输入/输出 token；不保存聊天内容 |

上游一律使用官方 `https://api.openai.com/v1/responses`，`store=false`、`stream=true`。非流式客户端在收到 `response.completed` 后获得结果。不自动重试推理，不轮换账号，不切换付费 API。

首版仅验证 function 工具，不提供 ChatGPT 网页的完整工具环境。工具执行由调用方应用负责。音频、视频、生图、Embeddings、托管工具、服务端历史引用均未提供。不能承诺兼容所有客户端。

`temperature`、`top_p`、`max_tokens`、`max_completion_tokens`、`max_output_tokens`、`stop` 等未实现参数会明确返回400，不会悄悄忽略。请关闭客户端自动附加的这些参数；后续按实际应用需求扩展并验证。`n` 仅允许1。显式 model 优先；省略 model 使用页面默认值。

Continue 一键连接使用专用入口 `/compat/continue/v1`，明确接受并忽略 Continue 自动发送的采样参数、`max_tokens` 和 `max_completion_tokens`，实际输出上限由上游控制；响应头 `X-Local-Plan-Ignored-Parameters` 列出忽略的参数。非空 stop 指令仍明确报错。普通 `/v1` 入口保持严格参数验证。

连接 SDK 时建议设置 `max_retries=0`，避免调用方自动重试造成重复请求。网关本身不会自动重试推理。

## 验证与隐私

离线测试：`python -m unittest discover -s tests -v`。使用模拟上游验证协议，不消耗套餐。真实账号需要用户在官方页面完成登录后，才能验证实际模型调用；不要把模拟测试描述成真实授权或推理成功。

凭据和本地 API 密钥不进入源码和发行包，OAuth 回调 URL 不记录访问日志。退出时清除本地账号，尽力撤销远端会话；远端撤销未确认会明确提示。只监听127.0.0.1，不提供公共代理服务。

## 官方依据

- [本地开源应用的套餐授权](https://developers.openai.com/siwc/token-sharing-open-source)
- [登录、PKCE、身份验证](https://developers.openai.com/siwc/token-sharing-open-source/sign-in)
- [模型目录与流式完成条件](https://developers.openai.com/siwc/token-sharing-open-source/models-and-inference)
- [凭据与会话管理](https://developers.openai.com/siwc/token-sharing-open-source/profiles-and-sessions)

套餐额度由 OpenAI 计算，和同账号其他应用共享；本工具不把 token 数换算成精确套餐百分比。收费或远程托管产品需按官方要求另行申请。实际可用能力取决于用户账号、授权和官方支持范围。

## 界面与开机自启动

应用连接按独立卡片折叠，点击应用名称展开连接、检查与撤销操作。高级 API 地址和密钥继续单独折叠。

在“启动设置”中开启“开机自启动”，即可在当前 Windows 用户登录后静默启动后台桥，不弹出浏览器，也不需要管理员权限。默认关闭。开关读取 Windows 当前启动项状态，关闭时仅移除 LocalPlanAPI 的启动项。程序移动到其他目录后，请重新开启开关以更新路径。尚未进行 Windows 注销/登录实测。

## 默认思考深度

在默认模型下方选择自动（不额外传参）、低、中、高，并点击保存设置。桥在应用未显式传入 reasoning.effort 时填入默认深度；显式请求优先，并保留其他 reasoning 设置。修改仅影响后续新请求，默认保持自动。具体支持由上游模型决定。参考：https://developers.openai.com/api/docs/guides/reasoning 。

## 用量记录与界面语言

Token 用量记录支持最近 24 小时、7 天、30 天及全部记录，每页 20 条，页面可见时每 15 秒刷新。显示请求时间、模型、完成状态、输入、输出及合计。未收到上游报告的请求标记为未报告；旧数据保留原值并标记旧记录。记录和汇总只统计本工具收到的用量，不代表套餐剩余额度。

右上角可切换中文和 English；选择保存在本机浏览器，刷新页面仍保留。

## Cherry Studio

应用连接区提供 Cherry Studio 的独立折叠说明卡和官方下载入口：https://www.cherryai.com/download 。该应用可在内部直接登录 ChatGPT，无需本工具适配或填写桥的 API 密钥。

## 固定目录与版本管理

日常使用始终打开项目根目录的 `open.cmd`。已编译程序固定在 `dist/LocalPlanAPI/`，以后在原位置更新；保留完整文件夹。源码和版本记录使用当前工作区 Git 仓库管理，构建产物、虚拟环境及运行凭据不进入 Git。

`python release.py` 只准备固定程序目录中的说明文件，不生成压缩包。只有需要分享给别人时才执行 `python release.py --zip`，导出固定名称 `output/local-plan-api/LocalPlanAPI-Windows.zip`，重复导出覆盖同一个文件。旧版压缩包已集中移至 `output/local-plan-api/archive/`。

高级连接中分别展示通用地址 `/v1` 与应用兼容地址 `/compat/continue/v1`，每项提供独立复制按钮。AnythingLLM 手动连接时可使用应用兼容地址和本地通用密钥。

## AnythingLLM 桌面版连接

应用连接区增加独立折叠卡片，支持连接、检查、停用并恢复原配置。需要桌面版 1.18+，通过官方 `anythingllm://set-provider` 链接预填配置，在 AnythingLLM 中点击 Use／使用后才保存。桥不直接修改应用文件，不重启应用；原 LLM 设置加密保存在桥的数据目录。

独立地址为 `/compat/anythingllm/v1`，使用单独生成的本地密钥和 `local-plan-default` 模型别名。应用明确指定的工作区模型优先。嵌入模型、向量数据库、文档不改动。检查验证配置和官方模型目录，不发送推理请求。

“停用并恢复原配置”会立即撤销专用密钥，并打开原配置的确认页；需要在 AnythingLLM 内确认才能恢复。如果取消，原配置不会恢复，可以再次点击此按钮。手动修改过 LLM 设置时拒绝覆盖。仅支持标准本机数据目录；Docker、云端或自定义数据目录暂不自动连接。

官方链接说明：https://docs.anythingllm.com/installation-desktop/deep-links 。

## 自动跟随模型更新

模型栏增加“自动跟随更新”开关，默认关闭，开启或关闭立即保存。支持命名为 `gpt-数字版本-luna/sol/astra/terra` 的稳定型号，只在同系列中按数值版本升级，不跨系列、不降级，排除预览、日期快照及其他无法识别的名称。当前账号必须实际开放目标型号。换账号或重新登录后重置开关。

刷新模型目录时检查，目录缓存最长 5 分钟；默认模型别名或省略模型的新请求会先刷新到期的目录，再决定型号。页面关闭时不在后台定时轮询，下一次相关请求才检查。更新结果及最近一次切换显示在模型栏，已进行中的请求与应用明确指定型号的请求保持不变。新型号的真实效果和接口兼容性仍由上游决定。

## Open WebUI / Windows Docker Desktop

在 Open WebUI（Docker）卡片启用专用连接，复制地址 `http://host.docker.internal:24664/compat/openwebui/v1` 和专用密钥，在 Open WebUI 管理员设置的 OpenAI API 连接中保存。模型 ID 可留空，聊天时选择 `local-plan-default` 跟随桥的默认值。不要使用浏览器 Direct Connections。

只监听原有 127.0.0.1 地址。对 Windows Docker Desktop 的本机转发，仅在启用专用密钥后允许 host.docker.internal 主机名访问该应用的 GET models / POST chat/completions。其他路由、外部 Origin、非本机来源仍拒绝；通用 API 密钥不能用于此入口。禁用立即撤销专用密钥，不改其他应用。该路径取决于 Docker Desktop 本机转发机制，不宣称兼容其他 Docker 网络环境。

## Open WebUI

Open WebUI（Docker）卡片增加一键配置和撤销按钮。适用于当前 Windows Docker Desktop、容器名 `open-webui`、标准 `/app/backend/data/webui.db` SQLite 存储。连接配置先备份到桥的 DPAPI 加密存储；以事务和并发校验仅修改数据库的 OpenAI 连接部分，保留其他连接与数据，然后重启 Open WebUI 使配置生效。正在处理桥请求时拒绝配置。重复配置不会新增重复条目。后续手动修改过连接配置时，撤销会拒绝覆盖；仍可独立停用专用密钥。重启可能暂时断开网页连接，请先结束对话。模型选择仍由网页控制，请刷新并选择 `local-plan-default`。

四款应用图标改为官方项目素材，来源与标识权属记录在 `plan_api/assets/README.md`，图片内置在页面中，运行时不访问外部图床。

## Cline Desktop

在应用连接中展开 Cline（桌面版），点击一键配置。工具在本机 Cline v1 格式的 `providers.json` 和 `models.json` 中添加独立的 **ChatGPT · Local Plan API** 提供商，不改动原来的提供商、账号或会话。重新进入 Cline 的 Providers 页面，选择此提供商和 `local-plan-default`；未出现时重开 Cline。

连接使用 `/compat/cline/v1` 和专用密钥。检查连接只核对配置、密钥及模型目录，不发送推理请求。撤销会移除本工具添加的提供商并停用其密钥；如该提供商被手动修改，会拒绝覆盖。恢复记录使用 Windows DPAPI 加密。写入两个配置文件时若失败，会尝试回滚并保留重试信息。

当前适配 Cline Desktop 的共享 v1 JSON 配置；不修改旧版 VS Code 扩展配置。自定义配置目录通过启动桥时的 `CLINE_DIR` 或 `CLINE_DATA_DIR` 指定。首次使用前请安装、启动 Cline 并完成初始设置。

### Cline VS Code extension

应用连接中的 Cline（VS Code）独立配置共享 v1 提供商目录，使用 `/compat/cline-vscode/v1` 和单独密钥。配置后重新加载 VS Code 窗口，在扩展 API Provider 选择 `ChatGPT · Local Plan API`，模型选择 `local-plan-default`。当前适配已检查的 Cline 4.1.25；旧扩展可能不读取该共享目录。它不直接修改 VS Code SQLite 状态库或加密密钥库，也不会自动发送测试消息。桌面版与扩展版可分别撤销。

## DeepSeek Harness

展开应用连接中的 DeepSeek Harness，点击一键配置。它为桌面 profile 的 `cordis.patch.yml` 添加 `llm-pi-ai.providers.local-plan-api`，密钥通过 `.credentials.yaml` 中的独立引用保存；桥内恢复记录用 DPAPI 加密。它保留其他模型、登录账号和插件配置，并与 Harness 的配置写入锁协调。

重新打开 Harness 的模型设置，选择 **ChatGPT · Local Plan API** / `local-plan-default`。若尚未显示，请重开 Harness。使用 `/compat/harness/v1` 的 OpenAI Chat Completions 兼容入口。检查不发送模型推理；实际聊天由用户测试。撤销只删除工具添加的提供商和密钥引用；手动改动的同名配置不会被覆盖。当前适配本机 0.2.1-rc.2 桌面 profile；自定义 home 通过 `DSH_HOME` 指定。


## AstrBot

展开 AstrBot 卡片，点击 **一键配置 AstrBot**。自动检测 Windows AstrBot Launcher 的单个实例，新增 `local-plan-api` 提供商和 `local-plan-api/local-plan-default` 模型，使用 `/compat/astrbot/v1` 专用入口与独立本地密钥。其他本机安装可在启动桥前设置 `ASTRBOT_ROOT`，指向包含 `data/cmd_config.json` 的 AstrBot 根目录。多个实例必须明确指定；暂不自动配置 Docker、WSL 或远程实例。

**写入后先重启 AstrBot，再保存 AstrBot 的设置**，避免旧进程覆盖新配置。然后在 AstrBot 服务提供商/模型选择中选择 **local-plan-api/local-plan-default**。原默认模型、其他提供商、QQ/微信平台和插件配置保留，不会自动切换机器人模型或重启机器人。默认别名跟随桥页面的模型设置。

检查只核对磁盘配置、专用密钥和官方模型目录，不发送聊天，也不能证明运行中的 AstrBot 已加载配置。兼容入口接受并忽略客户端自动附带的采样参数及输出 token 上限，实际限制由上游控制；其他不支持的参数仍返回错误。图片和 function tools 使用现有协议转换，实际对话和工具能力留给用户验证；不提供嵌入模型。撤销前请先切换到其他模型；撤销只移除本工具添加的两项并停用专用密钥，随后需重启 AstrBot。原配置备份由 DPAPI 加密。
