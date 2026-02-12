# chasingclaw（中文文档）

`chasingclaw` 是一个超轻量的个人 AI 助手框架。

English version: [README.md](./README.md)

## 本版本更新

- 项目已从 `nanobot` 全量重命名为 `chasingclaw`
- 移除了 logo/case 等示例图片资源
- 新增 Web UI 服务（默认端口 `18789`）
- 新增可配置的 Webhook 请求/回调能力

## 安装

### 从源码安装

```bash
git clone https://github.com/HKUDS/chasingclaw.git
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

## Web UI（端口 18789）

启动：

```bash
chasingclaw ui --port 18789
```

打开：

- `http://localhost:18789`

UI 功能：

- 配置模型供应商（provider）、模型名（model）、API Key、API Base URL
- 基于浏览器本地保存的 session id 持续对话
- 查看当前会话历史消息
- 配置 Webhook 请求地址与回调地址

## Webhook 协议

内置 webhook 入口（由 `chasingclaw ui` 提供）：

- `POST /api/webhook/request`

请求示例：

```json
{
  "message": "请总结今天的任务",
  "sessionId": "team-a",
  "requestUrl": "https://example.com/request-hook",
  "callbackUrl": "https://example.com/callback-hook"
}
```

响应示例：

```json
{
  "ok": true,
  "sessionId": "team-a",
  "reply": "...assistant response...",
  "requestRelay": {
    "ok": true,
    "status": 200,
    "body": "..."
  },
  "callback": {
    "ok": true,
    "status": 200,
    "body": "..."
  }
}
```

说明：

- `requestUrl` 与 `callbackUrl` 也可在 Web UI 中配置并持久化到配置文件
- 若配置了 `requestUrl`，请求会先被转发到该地址
- 助手生成回复后，会将结果回调到 `callbackUrl`

## 配置说明

主配置文件：

- `~/.chasingclaw/config.json`

常用配置项：

- `providers.*`：API Key / API Base
- `agents.defaults.model`：默认模型
- `tools`：shell/web 工具行为
- `channels`：Telegram/Discord/Slack/Email 等渠道
- `channels.webhook`：Webhook 请求/回调配置

## Docker

容器暴露端口：

- `18789`（Web UI）
- `18790`（Gateway）

## 安全

请参考：

- `SECURITY.md`

## 许可证

MIT
