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

UI 主要能力：

- 配置模型供应商（provider）、模型名（model）、API Key
- provider 支持 `custom`，且仅 `custom` 可填写 API Base URL
- 浏览器持久化 session，支持连续对话
- 查看当前会话历史
- 配置“智慧财信机器人webhook地址”（出站发送地址）
- 展示只读“用于配置智慧财信机器人的webhook回调地址”（chasingclaw 入站地址）
- 点击“测试智慧财信发送”可直接向智慧财信机器人发送测试消息

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

发送格式：

```json
{
  "msgtype": "text",
  "text": {
    "content": "...assistant response..."
  }
}
```

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

## 配置说明

主配置文件：

- `~/.chasingclaw/config.json`

常用配置项：

- `providers.*`：API Key / API Base
- `agents.defaults.model`：默认模型
- `tools`：shell/web 工具行为
- `channels`：Telegram/Discord/Slack/Email 等渠道
- `channels.webhook.callback_url`：智慧财信机器人 webhook 出站地址

## 常见问题

- callback 地址保存失败：先确认 `http://<服务器IP>:18789/api/webhook/request` 可公网访问，且 GET 返回 `{"result":"ok"}`。
- 群里不回消息：检查智慧财信安全设置（关键词/IP 白名单/签名校验）是否放行。
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
