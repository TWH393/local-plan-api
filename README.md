# Local Plan API

免费、开源发布用的 Windows 本地 ChatGPT 套餐兼容网关。用户在自己的电脑上完成官方 OAuth 登录，应用请求通过本机接口使用该用户获准的套餐能力。

当前版本 0.2.5：独立于 QQ 机器人，不读取其账号、配置、记忆或密钥。默认端口 `24664`，凭据使用 Windows DPAPI 绑定当前 Windows 用户加密，存放在 `%LOCALAPPDATA%/LocalPlanAPI/`。

## 开始使用

1. 本机直接使用：双击项目根目录的 `open.cmd`；它会启动固定目录 `dist/LocalPlanAPI/LocalPlanAPI.exe` 并打开管理页。无需解压或安装 Python。源码运行：安装 Python 3.11+，双击 `start.cmd`。
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
