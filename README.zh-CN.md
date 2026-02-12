# chasingclaw（中文文档）

`chasingclaw` 是一个超轻量的个人 AI 助手框架。

English version: [README.md](./README.md)

## 本版本更新

- 项目已从 `nanobot` 全量重命名为 `chasingclaw`
- 移除了 logo/case 等示例图片资源
- 新增 Web UI 服务（默认端口 `18789`）
- 新增 Webhook 回调配置与入站地址展示能力

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
- 供应商支持 `custom` 选项，且只有 `custom` 才可填写 API Base URL
- 基于浏览器本地保存的 session id 持续对话
- 查看当前会话历史消息
- 配置“智慧财信机器人webhook地址”（出站回调地址）
- 查看只读的 chasingclaw 入站 webhook 地址，用于 IM 平台回调配置

## Webhook 协议

内置 webhook 入口（由 `chasingclaw ui` 提供）：

- `POST /api/webhook/request`

请求示例：

```json
{
  "message": "请总结今天的任务",
  "sessionId": "team-a",
  "callbackUrl": "https://example.com/callback-hook"
}
```

响应示例：

```json
{
  "ok": true,
  "sessionId": "team-a",
  "reply": "...assistant response...",
  "callback": {
    "ok": true,
    "status": 200,
    "body": "..."
  }
}
```

说明：

- `callbackUrl` 可在 Web UI（字段名“智慧财信机器人webhook地址”）中配置，或在请求体中传入
- UI 会展示只读的入站 webhook 地址（优先展示局域网 IP，而非 localhost），供 IM 平台配置回调
- 助手生成回复后，会将结果回调到 `callbackUrl`
- 可通过环境变量 `CHASINGCLAW_PUBLIC_HOST` 覆盖 UI 中展示的入站主机地址

## 配置说明

主配置文件：

- `~/.chasingclaw/config.json`

常用配置项：

- `providers.*`：API Key / API Base
- `agents.defaults.model`：默认模型
- `tools`：shell/web 工具行为
- `channels`：Telegram/Discord/Slack/Email 等渠道
- `channels.webhook`：Webhook 回调配置

## Docker

容器暴露端口：

- `18789`（Web UI）
- `18790`（Gateway）

## 安全

请参考：

- `SECURITY.md`

## 许可证

MIT
