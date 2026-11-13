# Google Flow provider for magpie

账号在 magpie 中管理，生图作为三个原生 provider 模型提供（Flow 菜单中的
Pro / 2.1 / 2 Lite），视频生成保留为 MCP tools。插件只有一个导出函数
`GflowPlugin`，同时返回 `config` / `provider` / `auth` / `tool` hooks；
`index.mjs` 是唯一入口。

## 安装

```bash
# 在 magpie 运行的机器上安装 gflow Python 包（含 gflow.magpie_bridge）
cd /path/to/google-flow-cli && python3 -m pip install -e .

# 安装本地插件目录；不要修改全局 magpie 账号文件
magpie plugin add /path/to/google-flow-cli/magpie-plugin
magpie library sync   # 如果 agent 暂时没发现新工具
```

插件选项：

```bash
magpie plugin options magpie-tools-gflow '{"python":"/path/to/venv/bin/python","cwd":"/path/to/work","gflowBin":"/path/to/venv/bin/gflow","stateDir":"/path/to/private/state"}'
```

可用环境变量：`GFLOW_PYTHON`、`GFLOW_CWD`、`GFLOW_BIN`、
`GFLOW_MAGPIE_STATE_DIR`、`GFLOW_MAGPIE_AUTH_PATH`、`CHROME_PATH`。

Python bridge 以 `python -I -c` 启动，并显式把本仓库根加入 `sys.path`，
不依赖 magpie 进程的 cwd、editable install 或 `PYTHONPATH`。未配置 `python`
时插件会自动探测 Python ≥3.10 且能导入 `gflow` / `requests` / `pydantic` /
`websocket` 的解释器；macOS 的 Xcode `python3` 若是 3.9 会被跳过。显式设置
`python` 或 `GFLOW_PYTHON` 时，该解释器必须满足同样条件，错误会直接提示。
Cookies 只通过 stdin 和进程内 header 传给 Python bridge，不进入 argv、
插件日志或 JSON 工具输出。

## 在 magpie 中登录账号

1. 打开 magpie Settings → Providers → **Google Flow**，点 **Add account**。
2. 推荐选择 **打开 Google Flow 获取 Cookie**：插件打开此账号独立的 Chrome
   窗口并停在 `https://flow.google.com/`；完成 Google 登录后，插件通过
   browser-level CDP 读取 Google 域 Cookie、确认 Flow 页面已加载，就立即保存
   登录。邮箱是 best-effort：先从 Flow app shell 的 `WIZ_global_data.oPEP7c`
   读取，读不到时再通过 `Target.attachToTarget` 从已登录页面读取；仍读不到也
   不会让 magpie 判定「登录未完成」，显示名回退到 account key / rename。
   `auth.usage` 还会用邮箱补齐旧账号的空 `metadata.email`。登录时把当前 magpie
   account key 记入 `metadata.gflowAccount`，使后续生图使用同一账号目录。Chrome
   会继续保留，供后续生图请求的 reCAPTCHA 使用。这个入口不需要从 DevTools
   手动复制 Cookie；插件不再把 Flow URL 交给 magpie 再开一个内置窗口。
3. 也可以选择 **Google Flow cookies / Google Flow Cookie header**，粘贴浏览器
   里 Flow 页面请求的完整 `Cookie` 请求头值，例如
   `SID=<redacted>; HSID=<redacted>; SSID=<redacted>; APISID=<redacted>; SAPISID=<redacted>`。
   插件调用 `gflow.magpie_bridge validate`，从 `flow.google.com/` shell 读取
   邮箱和额度；额度读不到时仍可保存 Cookie。旧 `labs.google/fx/api/auth/session`
   已不再使用。
4. 登录后在 magpie Usage 页可读取剩余额度：插件的 `auth.usage` 用同一 Cookie
   获取 `nzlxg` batchexecute 响应，把 Flow 账号菜单显示的 credits 数映射为
   `balance` / `windows[].balance`。额度读取失败时仍返回可识别的 `user` 和明确
   错误，不显示虚构数字；顺便通过 host `auth.set` 补齐旧账号的空
   `metadata.email`。Usage 页按 magpie 的缓存/刷新机制读取该值。
5. 可以重复添加多个 Google 账号；每个账号的 CDP/CDP profile 状态按账号 ID
   隔离在 `~/.config/magpie/gflow/accounts/<safe-id>/`（可用 `stateDir` 改位置）。

Cookie 是 Google 登录凭据，不要粘贴到聊天、issue 或日志里。自动入口会打开一个
真实 Chrome 窗口供用户自己完成 Google 登录，不会代替用户输入密码或绕过登录。

## 原生生图（不是 MCP tool）

三个模型通过 `config.provider` + `provider.models` 注册为 magpie 的
OpenAI-compatible image models。任何 agent 走 magpie gateway 的
`POST /images/generations` 调用，`model` 字段选择具体模型；插件的
`auth.loader.fetch` 拦截该请求并调用 Python bridge，返回 OpenAI 风格的
`{data:[{b64_json:"..."}]}`。MCP tools 只剩视频相关入口。

| 模型 id | Settings 显示名 | Flow wire `imageModelName` |
| --- | --- | --- |
| `gflow/nano-banana-pro` | Google Flow · Nano Banana Pro | `GEM_PIX_2` |
| `gflow/nano-banana-2.1` | Google Flow · Nano Banana 2.1 | `BELUGA` |
| `gflow/nano-banana-2-lite` | Google Flow · Nano Banana 2 Lite | `HARBOR_SEAL` |

```bash
magpie tool generate_image --model gflow/nano-banana-pro --prompt "a red fox in the snow"
magpie tool generate_image --model gflow/nano-banana-2.1 --prompt "a red fox in the snow"
magpie tool generate_image --model gflow/nano-banana-2-lite --prompt "a red fox in the snow"
```

其它 agent 直接发标准请求（gateway 会把 `model` 透传给插件）：

```bash
curl -s "$MAGPIE_GATEWAY/v1/images/generations" \
  -H "Authorization: Bearer $MAGPIE_KEY" -H 'Content-Type: application/json' \
  -d '{"model":"gflow/nano-banana-2.1","prompt":"a red fox in the snow","n":1}'
```

调用链：

```text
agent → magpie POST /images/generations
  → GflowPlugin.auth.loader.fetch（拦截该账号请求，规范化 model）
  → python3 -I -c <bootstrap> → gflow.magpie_bridge image（stdin JSON + cookie header）
  → GenerateImageRequest.model → IMAGE_MODELS[model] → FlowClient.generate_image()
  → aisandbox-pa flowMedia:batchGenerateImages（imageModelName）
  → OpenAI-style {data:[{b64_json:"..."}]}
```

未知 `model` 直接返回 400，不会静默换成别的模型。`size` 支持
`1024x1024` / `1024x1792` / `1792x1024` 及 `square` / `portrait` /
`landscape`；`n` 为 1–8。返回的是 `b64_json` 图片数据，不是文件路径。
请求中如带 `seed`，bridge 会转发给 `GenerateImageRequest`。CLI 也支持
`gflow generate-image PROMPT --model <id>`。

Flow 生图需要真实浏览器 reCAPTCHA：账号对应的 Chrome/CDP 会话必须预先在
`GFLOW_HOME` 指向的目录里放好 `cdp-port`，并有打开的
`https://flow.google.com/` Flow 页面。bridge 设置
`GFLOW_NONINTERACTIVE=1`，若 CDP 不可用会直接返回错误，绝不自动启动浏览器或
写入 `~/.gflow/env`。

## 视频 MCP tools

| Tool | 说明 |
| --- | --- |
| `gflow_generate_video` | `gflow generate-video --json`，可 `--wait` / `--no-wait` |
| `gflow_extend_video` | `gflow extend-video --json`，需要 `media_id` |
| `gflow_auth_status` | 只读：CLI 认证、magpie 账号数量、原生模型名 |

视频 tools 仍通过已认证的 `gflow` CLI 使用 `~/.gflow` session；这是明确的
兼容限制：它们不读取 magpie 账号的 cookie、不参与账号切换。执行前会先检查
`gflow auth --status`，避免交互式浏览器登录挂起工具调用。旧的
`gflow_generate_image` MCP tool 已移除，避免与原生生图模型重复；CLI 的
`gflow generate-image` 命令仍保留。

## 限制与回滚

- 每个账号仍需一个存活的、已登录 Flow 的 Chrome CDP 窗口；reCAPTCHA 失败、
  cookie 过期或无 CDP 会返回 401/502，不会在生成请求里再次自动启动浏览器。
- 没有调用真实 Google Flow 生成；自动化测试全部使用 mock。
- 卸载：`magpie plugin remove magpie-tools-gflow`；删除插件目录和删除
  `~/.config/magpie/gflow/accounts/` 中本插件创建的 per-account 状态。
  `plugin-auth.json` 的清理用 magpie UI 的 sign out / remove account，不要手工
  编辑共享文件。原 `~/.gflow/env` 与 CLI 行为不受本插件改动影响。

## 测试

```bash
cd /path/to/google-flow-cli && python3 -m pytest -q
cd magpie-plugin && npm test
node --check index.mjs && node --check gflow.mjs && node --check provider.mjs
```
