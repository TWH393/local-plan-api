# Local Plan API

[中文](README.md) | **English**

A free, open-source Windows gateway for connecting local applications to supported ChatGPT plan capabilities. Each user signs in and authorizes access through the official OAuth flow on their own computer. Applications then use the capabilities granted to that account through a local API.

Current version: **0.3.8**. This tool manages its ChatGPT authorization independently. The default port is `24664`. Credentials are encrypted with Windows DPAPI, bound to the current Windows user, and stored in `%LOCALAPPDATA%/LocalPlanAPI/`.

## Current status

This is an early testing release, primarily for local use on Windows. The interface supports Chinese and English, model and reasoning settings, usage history, and startup at Windows sign-in.

| Application | Status |
|---|---|
| Continue / AnythingLLM / Open WebUI | Connections have been verified in a user's environment. Automatic setup can still depend on application versions. |
| Cline Desktop / Cline VS Code | Separate setup and undo are available. Some client sessions have had API key loading problems; further testing is needed. |
| DeepSeek Harness | Desktop profile configuration has been written successfully. Actual conversations still await user testing. |
| AstrBot | Local setup, checks and undo implemented; actual chat awaits user testing |
| Cherry Studio | Download link and instructions for signing in within the application. |

**Check connection** checks local configuration, keys, and the model catalog. It does not send an inference request or establish that every tool feature works.

## Getting started

1. After downloading the source from GitHub, install Python 3.11+ for Windows, including the Python Launcher, and double-click `start.cmd`. The first launch creates a virtual environment and installs dependencies. The repository does not include a compiled EXE. If you have built the portable application, use `open.cmd` to launch `dist/LocalPlanAPI/LocalPlanAPI.exe`.
2. On the local dashboard, click **Continue with ChatGPT** and complete sign-in and authorization on the official OpenAI website.
3. Click **Connect Continue**. The gateway detects a standard local installation, backs up the configuration, and adds the connection without requiring you to copy keys or edit YAML.
4. In Continue, select **ChatGPT · Local Plan API**. Reload the VS Code window if it does not appear. You can then change the default model on the gateway dashboard without editing Continue again.

Setup keeps the selected model if it is available. Otherwise, it prefers GPT-6 Luna when listed for the account, or uses the first available model. Install the Continue extension and authorize plan access first. Automatic setup supports standard local Continue installations in VS Code, Insiders, and VSCodium. It does not configure remote SSH, WSL, or custom extension directories.

Other models, rules, and comments are preserved. Backups are encrypted with DPAPI. Repeated setup does not add duplicate entries. **Undo connection** removes only the entry added by this tool: if the file is unchanged, it restores the original content; if other changes were made, it removes only the managed entry. Manually modified connection entries are not forcibly overwritten or deleted.

Continue uses a dedicated local key for its compatibility endpoint. Undo revokes that key without affecting the general API key. Requests normally use the `local-plan-default` alias, which the gateway resolves to the current default model. Connection checks do not send prompts to a model; successful configuration and catalog checks do not prove successful inference.

On Windows, the gateway runs as a separate background process. Opening another project or closing the VS Code terminal does not stop it. Launching the same version again opens the dashboard. A newer version asks the running version to exit normally; it does not force termination while a request is active.

To stop the gateway, click **Stop local service** at the bottom of the dashboard. For terminal debugging, use `python run.py --foreground`. Closing the browser does not stop the service. Keep the entire portable folder; copying the EXE alone is insufficient.

Default Base URL: `http://127.0.0.1:24664/v1`. This tool generates its own local API key, separate from OpenAI OAuth credentials. Do not paste OAuth tokens into other applications.

You can also launch with `python run.py --port 24664 --no-browser`. Starting again on the same port does not create another instance. Diagnostic logs are stored at `%LOCALAPPDATA%/LocalPlanAPI/runtime.log` and `error.log`. OAuth callback access requests are not logged.

## API compatibility

| Endpoint or capability | Initial implementation |
|---|---|
| GET /v1/models | Official model catalog for the current account, preserving actual model IDs |
| POST /v1/responses | Non-streaming responses or actual SSE forwarding |
| POST /v1/chat/completions | Conversion of messages, image URLs/data URLs, function tools, tool results, and structured output |
| Chat streaming | Text and function-argument deltas, finish_reason, and optional usage |
| Cancellation | Closes the upstream connection when the client disconnects |
| Incomplete responses / quota errors | Explicit errors; failures after streaming begins use SSE errors rather than a successful finish_reason |
| Usage | Records request time, model, status, and upstream-reported input/output tokens; does not save conversation content |

Upstream requests use the official `https://api.openai.com/v1/responses` endpoint with `store=false` and `stream=true`. Non-streaming clients receive results after `response.completed`. The gateway does not automatically retry inference, rotate accounts, or fall back to paid API access.

The initial implementation validates function tools, rather than providing the complete ChatGPT web tool environment. The calling application executes tools. Audio, video, image generation, embeddings, hosted tools, and server-side history references are not provided. Compatibility with every client is not guaranteed.

Unsupported parameters such as `temperature`, `top_p`, `max_tokens`, `max_completion_tokens`, `max_output_tokens`, and `stop` produce explicit HTTP 400 errors rather than being silently ignored on the general endpoint. Disable automatically added parameters in the client. Additional support will be based on specific applications and testing. Only `n=1` is supported. An explicitly requested model takes priority; an omitted model uses the dashboard default.

Continue's dedicated `/compat/continue/v1` endpoint explicitly accepts and ignores the sampling parameters, `max_tokens`, and `max_completion_tokens` that Continue adds automatically. The upstream service controls the actual output limit. The `X-Local-Plan-Ignored-Parameters` response header lists ignored parameters. Nonempty stop instructions still produce an error. The general `/v1` endpoint retains strict parameter validation.

For SDK clients, consider setting `max_retries=0` to prevent automatic retries from generating duplicate requests. The gateway itself does not retry inference.

## Verification and privacy

Offline tests: `python -m unittest discover -s tests -v`. They use a simulated upstream service and do not consume plan usage. Actual account authorization and inference can only be checked after the user signs in on the official website. Simulated tests should not be presented as successful real authorization or inference.

Credentials and local API keys are excluded from source code and release packages. OAuth callback URLs are not access-logged. Disconnecting clears the local account and attempts to revoke the remote session; the interface reports when remote revocation cannot be confirmed. The service listens only on `127.0.0.1` and does not provide a public proxy.

## Official references

- [Plan access for local open-source applications](https://developers.openai.com/siwc/token-sharing-open-source)
- [Sign-in, PKCE, and identity validation](https://developers.openai.com/siwc/token-sharing-open-source/sign-in)
- [Model catalog and streaming completion](https://developers.openai.com/siwc/token-sharing-open-source/models-and-inference)
- [Credentials and session management](https://developers.openai.com/siwc/token-sharing-open-source/profiles-and-sessions)

OpenAI calculates plan usage, shared with other applications on the same account. This tool does not convert token counts into an exact remaining-plan percentage. Paid or remotely hosted products require a separate application under the official requirements. Actual capabilities depend on the account, authorization, and officially supported scope.

## Interface and Windows startup

Application connections use separate collapsible cards. Click an application's name to expand its setup, check, and undo controls. Advanced API addresses and keys are in a separate collapsible section.

Enable **Start with Windows** under startup settings to launch the background gateway silently when the current Windows user signs in. It does not open a browser or require administrator privileges. This is disabled by default. The toggle reads the current Windows startup entry; disabling it removes only LocalPlanAPI's entry. If you move the program, toggle the setting again to update the path. Windows sign-out/sign-in behavior has not yet been tested end to end.

## Default reasoning effort

Below the default model, select automatic (no extra parameter), low, medium, or high, then save. The gateway adds this default when the application does not explicitly supply `reasoning.effort`. Explicit request values take priority, and other reasoning settings are preserved. Changes affect subsequent requests only. The default is automatic; upstream models determine supported values. See the [reasoning guide](https://developers.openai.com/api/docs/guides/reasoning).

## Usage history and interface language

Token history supports the last 24 hours, 7 days, 30 days, or all records, with 20 entries per page. It refreshes every 15 seconds while the page is visible. Records show request time, model, completion status, input, output, and total tokens. Requests without an upstream usage report are marked as unreported. Legacy records retain their original values and are marked accordingly. Records and totals reflect only usage received by this tool, not remaining plan quota.

The language selector at the top right switches between Chinese and English. The choice is saved in the local browser and survives a refresh.

## Cherry Studio

The application section includes a separate Cherry Studio card and an [official download link](https://www.cherryai.com/download). The application can sign in to ChatGPT internally, so this gateway's adapter and API key are not required.

## Fixed program folder and version control

For everyday use, open `open.cmd` in the project root. The compiled application stays in `dist/LocalPlanAPI/` and is updated in place. Keep the entire folder. Git tracks source and version history; build output, the virtual environment, and runtime credentials are excluded.

`python release.py` prepares documentation in the fixed program folder without creating a ZIP. When sharing a portable build, use `python release.py --zip` to export `output/local-plan-api/LocalPlanAPI-Windows.zip`. Repeated exports overwrite the same file. Older local archives have been moved to `output/local-plan-api/archive/`.

Advanced connections show separate copy buttons for the general `/v1` address and the `/compat/continue/v1` compatibility address. Manual AnythingLLM setup can use the compatibility address and the general local API key.

## AnythingLLM Desktop

The separate application card supports setup, checks, and disabling the connection while restoring previous settings. AnythingLLM Desktop 1.18+ is required. Setup uses the official `anythingllm://set-provider` deep link to prefill settings; they are saved only after you click **Use** within AnythingLLM. The gateway does not edit application files or restart the application. Previous LLM settings are encrypted in the gateway's data directory.

The dedicated address is `/compat/anythingllm/v1`, with its own generated local key and the `local-plan-default` alias. Explicit workspace model selections take priority. Embedding settings, vector databases, and documents are not changed. Checks validate configuration and the official model catalog without sending inference requests.

**Disable and restore previous settings** immediately revokes the dedicated key and opens a confirmation page with the previous settings. You must confirm within AnythingLLM to restore them. Canceling does not restore the settings; you can click the button again. Manually changed LLM settings are not overwritten. Automatic setup supports the standard local desktop data directory, rather than Docker, cloud installations, or custom data directories.

See the [official deep-link documentation](https://docs.anythingllm.com/installation-desktop/deep-links).

## Automatically following model updates

The model section includes an **Automatically follow updates** toggle, disabled by default. Changes to the toggle are saved immediately. It recognizes stable models named `gpt-<numeric version>-luna/sol/astra/terra` and upgrades only within the same family by numerical version. It does not change families or downgrade, and excludes previews, dated snapshots, and unrecognized names. The current account must have access to the target model. Switching accounts or signing in again resets the toggle.

Updates are checked when refreshing the model catalog, which is cached for up to five minutes. New requests using the default alias or an omitted model refresh an expired catalog before choosing a model. There is no periodic background polling while the page is closed; the next relevant request triggers the check. The model section shows the result and most recent automatic switch. Active requests and requests specifying an explicit model are unaffected. The upstream service determines new models' behavior and API compatibility.

## Open WebUI / Windows Docker Desktop

Enable the dedicated connection in the Open WebUI (Docker) card. Copy `http://host.docker.internal:24664/compat/openwebui/v1` and its dedicated key into an OpenAI API connection in Open WebUI's administrator settings. Model IDs can be left empty. Select `local-plan-default` in a chat to follow the gateway default. Do not use browser Direct Connections.

The gateway continues to listen on `127.0.0.1`. For Windows Docker Desktop's local forwarding, it accepts the `host.docker.internal` hostname only for this application's GET models / POST chat/completions routes, and only while its dedicated key is enabled. Other routes, external Origins, and non-local sources are rejected. The general API key cannot authenticate this endpoint. Disabling immediately revokes the dedicated key without affecting other applications. This path depends on Docker Desktop's local forwarding behavior; other Docker network environments are not claimed to be supported.

## Open WebUI

The Open WebUI (Docker) card offers automatic setup and undo. It supports Windows Docker Desktop, a container named `open-webui`, and the standard SQLite database at `/app/backend/data/webui.db`. Connection settings are first backed up to the gateway's DPAPI-encrypted storage. Transactions and concurrency checks limit edits to the database's OpenAI connection section, preserving other connections and data. Open WebUI is then restarted to load the settings. Setup is refused while the gateway is processing a request. Repeated setup does not add duplicates. Undo refuses to overwrite subsequent manual connection changes; the dedicated key can still be disabled separately. Restarting can temporarily disconnect the web page, so finish active conversations first. Refresh the page and select `local-plan-default`.

The four application icons covered by the original UI update use official project assets. Sources and trademark ownership are documented in `plan_api/assets/README.md`. Images are embedded in the page and do not require external image hosting at runtime.

## Cline Desktop

Expand the Cline (Desktop) card and click automatic setup. The gateway adds a separate **ChatGPT · Local Plan API** provider to the local v1 `providers.json` and `models.json` files, preserving existing providers, accounts, and sessions. Reopen Cline's Providers page and select this provider with `local-plan-default`. Restart Cline if it does not appear.

The connection uses `/compat/cline/v1` and a dedicated key. Checks verify configuration, keys, and the model catalog without sending inference requests. Undo removes the managed provider and revokes its key. If that provider was manually modified, it refuses to overwrite the changes. Recovery records are encrypted with Windows DPAPI. If writing either configuration file fails, setup attempts rollback and preserves recovery information for retry.

This integration targets Cline Desktop's shared v1 JSON configuration and does not modify legacy VS Code extension configuration. Set `CLINE_DIR` or `CLINE_DATA_DIR` when starting the gateway to use a custom directory. Install and launch Cline and complete its initial setup first.

### Cline VS Code extension

The Cline (VS Code) card adds a separate provider to the shared v1 directory, using `/compat/cline-vscode/v1` and its own key. Reload the VS Code window, select **ChatGPT · Local Plan API** under API Provider, and choose `local-plan-default`. The installed Cline 4.1.25 implementation was inspected; older extensions may not read this directory. The gateway does not directly edit VS Code's SQLite state or encrypted secret storage and does not send automatic test messages. Desktop and extension connections can be undone separately.

## DeepSeek Harness

Expand the DeepSeek Harness card and click automatic setup. It adds `llm-pi-ai.providers.local-plan-api` to the desktop profile's `cordis.patch.yml`. The key is stored through a separate reference in `.credentials.yaml`; gateway recovery records are DPAPI-encrypted. Other models, signed-in accounts, and plugin configuration are preserved, and writes coordinate with Harness' configuration locks.

Reopen Harness' model settings and select **ChatGPT · Local Plan API** / `local-plan-default`. Restart Harness if it is not listed. The connection uses the OpenAI Chat Completions compatibility endpoint at `/compat/harness/v1`. Checks do not run inference; actual conversations are left for user testing. Undo removes only the managed provider and key reference, and refuses to overwrite manual edits to the same entries. The current integration targets the installed 0.2.1-rc.2 desktop profile. Set `DSH_HOME` for a custom home directory.


## AstrBot

Expand the AstrBot card and click **Configure AstrBot**. The gateway detects a single Windows AstrBot Launcher instance and adds the `local-plan-api` provider and `local-plan-api/local-plan-default` model, using `/compat/astrbot/v1` with a dedicated local key. For other local installations, set `ASTRBOT_ROOT` before starting the gateway to the AstrBot root containing `data/cmd_config.json`. Multiple instances require an explicit root. Docker, WSL and remote instances are not configured automatically.

**Restart AstrBot after setup, before saving its settings**, so the old process cannot overwrite the new configuration. Then select **local-plan-api/local-plan-default** in AstrBot's provider/model selector. Existing defaults, providers, QQ/WeChat platforms and plugins are preserved. The gateway does not switch the bot's model or restart the bot automatically. The alias follows the model selected on the gateway page.

Checks validate the saved configuration, dedicated key and official model catalog without sending chat messages. They cannot prove that the running AstrBot has loaded the configuration. The compatibility endpoint accepts and ignores automatically attached sampling parameters and output token limits; upstream controls the actual limits. Other unsupported parameters return errors. Images and function tools use the existing protocol conversion; real bot conversations and tools await user testing. Embeddings are not provided. Switch to another model before undoing the connection. Undo removes only the two managed entries and revokes the dedicated key; restart AstrBot afterwards. The original configuration backup is DPAPI-encrypted.
