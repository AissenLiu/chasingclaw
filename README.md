# chasingclaw

`chasingclaw` 是一个轻量级个人 AI 助手框架，支持命令行、Web UI 和多渠道接入。

## 安装

### 从源码安装

```bash
git clone https://github.com/AissenLiu/chasingclaw.git
cd chasingclaw
pip install -e .
```

### 从 PyPI 安装

```bash
pip install chasingclaw-ai
```

## 运行环境要求

- Python `>= 3.11`
- 操作系统：macOS / Linux / Windows（建议部署在 Linux 服务器）
- 网络：
  - 服务器需可访问你选择的模型供应商 API
  - 服务器需可访问智慧财信 webhook 出站地址
  - 智慧财信平台需可访问 chasingclaw 入站地址
- 端口：
  - `18789`（Web UI + webhook 入站）
  - `18790`（gateway，按需启用）
- 可选依赖：
  - 若要使用 `bridge` 相关能力（如 WhatsApp bridge 构建），需要 Node.js `>= 20` 和 `npm`
- 时间要求：
  - 使用 webhook 签名时请确保服务器时间准确（建议启用 NTP），避免 `DATE` 过期导致验签失败

## 快速开始

```bash
chasingclaw onboard
chasingclaw status
```

配置文件位置：

- `~/.chasingclaw/config.json`

## 常用命令

- `chasingclaw onboard`：初始化配置与工作目录
- `chasingclaw agent -m "..."`：单轮对话
- `chasingclaw agent`：交互式对话
- `chasingclaw gateway`：启动渠道网关
- `chasingclaw ui`：启动 Web UI（默认 `0.0.0.0:18789`）
- `chasingclaw channels status`：查看渠道状态
- `chasingclaw cron ...`：定时任务管理

## Web UI 使用（端口 18789）

启动：

```bash
chasingclaw ui --host 0.0.0.0 --port 18789
```

浏览器打开：

- `http://<服务器IP>:18789`

UI 可配置项：

- 常用配置（默认显示）：模型供应商（provider）、模型名（model）、API Key、智慧财信机器人 webhook 出站地址、chasingclaw 入站地址（只读）
- 高级配置（点击“高级配置”后显示）：
  - `custom` provider 的 API Base URL（仅 `custom` 可填写）
  - `tools.restrictToWorkspace` 开关（限制工具仅访问工作目录）
  - 智慧财信 webhook 请求超时（秒）
  - 智慧财信签名配置（key / secret）
  - 智慧财信回复消息类型（`text` / `markdown` / `link`）
  - 回复消息模板（支持 `{reply}` 占位符）
  - `link` 类型的 `title/messageUrl/btnTitle`
  - Cron 定时任务配置与管理

UI 交互能力：

- 浏览器持久化 session，支持连续对话
- 查看当前会话历史
- 一键“测试智慧财信发送”（直接向机器人 webhook 发测试消息）
- 可视化管理 cron 定时任务（新增 / 启用禁用 / 执行 / 删除）

## 智慧财信联通步骤

1. 在智慧财信群里创建自定义机器人，拿到机器人 webhook 地址。
2. 打开 chasingclaw Web UI，把该地址填入“智慧财信机器人webhook地址”，并保存。
3. 将 UI 中只读的“用于配置智慧财信机器人的webhook回调地址”填入智慧财信机器人 callback URL。
4. 智慧财信会先发起一次 GET 校验（chasingclaw 返回 `{"result":"ok"}`）。
5. 在群里 `@机器人` 发送消息，验证机器人是否自动回复。

## Webhook 协议

### 1) callback 可用性校验（智慧财信 -> chasingclaw）

- `GET /api/webhook/request`

响应：

```json
{"result":"ok"}
```

### 2) 入站消息（智慧财信 -> chasingclaw）

- `POST /api/webhook/request`

请求示例：

```json
{
  "chatid": 12345,
  "creator": 2324234,
  "content": "@webhook机器人 111",
  "reply": {
    "reply_content": "回复内容",
    "reply_creator": 1234
  },
  "robot_key": "xxx",
  "url": "https://xxxx",
  "ctime": 3452452
}
```

响应示例：

```json
{"result":"ok"}
```

### 3) 出站消息（chasingclaw -> 智慧财信机器人webhook地址）

`msgtype=text`：

```json
{
  "msgtype": "text",
  "text": {
    "content": "...assistant response..."
  }
}
```

`msgtype=markdown`：

```json
{
  "msgtype": "markdown",
  "markdown": {
    "text": "# 标题\n\n...assistant response..."
  }
}
```

`msgtype=link`：

```json
{
  "msgtype": "link",
  "link": {
    "title": "chasingclaw 回复",
    "text": "...assistant response...",
    "messageUrl": "https://example.com/detail",
    "btnTitle": "查看详情"
  }
}
```

签名配置（可选）：

- 当配置了 `sign_key + sign_secret`，出站请求会自动带：
  - `Content-Md5`
  - `Content-Type: application/json`
  - `DATE`
  - `Authorization: key:sha1(secret + Content-Md5 + Content-Type + DATE)`

说明：

- 文本长度按智慧财信要求限制为 5000 字符以内
- 出站目标地址来自 UI 配置项“智慧财信机器人webhook地址”

### 4) 本地调试兼容格式

为便于联调，`POST /api/webhook/request` 仍支持：

```json
{
  "message": "你好",
  "sessionId": "team-a",
  "callbackUrl": "https://example.com/callback"
}
```

## Cron 定时任务 API（UI 调用）

- `GET /api/cron/jobs?all=1`：获取任务列表
- `POST /api/cron/jobs`：新增任务（支持 `every` / `cron` / `at`）
- `POST /api/cron/toggle`：启用或禁用任务
- `POST /api/cron/run`：立即执行任务
- `POST /api/cron/remove`：删除任务

## 配置说明

主配置文件：

- `~/.chasingclaw/config.json`

常用配置项：

- `providers.*`：API Key / API Base
- `agents.defaults.model`：默认模型
- `tools.restrictToWorkspace`：是否限制工具访问在 workspace 内
- `channels.webhook.callbackUrl`：智慧财信机器人 webhook 出站地址
- `channels.webhook.timeoutSeconds`：出站请求超时
- `channels.webhook.signKey/signSecret`：签名配置
- `channels.webhook.messageType`：`text` / `markdown` / `link`
- `channels.webhook.messageTemplate`：回复模板（支持 `{reply}`）
- `channels.webhook.linkTitle/linkMessageUrl/linkButtonTitle`：link 类型消息配置

## 常见问题

- callback 地址保存失败：先确认 `http://<服务器IP>:18789/api/webhook/request` 可公网访问，且 GET 返回 `{"result":"ok"}`。
- 群里不回消息：检查智慧财信安全设置（关键词/IP 白名单/签名校验）是否放行。
- 签名校验失败：确认智慧财信后台 key/secret 与 UI 配置一致。
- UI 显示为 localhost：设置环境变量 `CHASINGCLAW_PUBLIC_HOST` 后重启，例如：

```bash
export CHASINGCLAW_PUBLIC_HOST=你的公网IP
chasingclaw ui --host 0.0.0.0 --port 18789
```

## Docker

容器暴露端口：

- `18789`（Web UI）
- `18790`（Gateway）

## 安全

- 参考 `SECURITY.md`

## 许可证

MIT
