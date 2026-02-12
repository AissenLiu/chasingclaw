# chasingclaw

中文文档（Chinese doc）: [README.zh-CN.md](./README.zh-CN.md)

`chasingclaw` is an ultra-lightweight personal AI assistant framework.

## What's New in This Version

- Project has been fully renamed from `nanobot` to `chasingclaw`
- Removed logo/case media assets and demo image bundles
- Added Web UI server (default port `18789`)
- Added webhook callback configuration and displayed inbound webhook address

## Install

### From source

```bash
git clone https://github.com/HKUDS/chasingclaw.git
cd chasingclaw
pip install -e .
```

### From PyPI

```bash
pip install chasingclaw-ai
```

## Quick Start

```bash
chasingclaw onboard
chasingclaw status
```

Then edit config at:

- `~/.chasingclaw/config.json`

## CLI Commands

- `chasingclaw onboard` - initialize config/workspace
- `chasingclaw agent -m "..."` - single-turn chat
- `chasingclaw agent` - interactive chat
- `chasingclaw gateway` - run channel gateway
- `chasingclaw ui` - start Web UI (default: `0.0.0.0:18789`)
- `chasingclaw channels status` - channel status table
- `chasingclaw cron ...` - cron task management

## Web UI (Port 18789)

Start UI:

```bash
chasingclaw ui --port 18789
```

Then open:

- `http://localhost:18789`

Web UI capabilities:

- Configure provider, model, API key, and API base URL
- Provider list includes `custom`; API Base URL can only be edited in `custom` mode
- Continue conversation by persistent session id in browser storage
- Inspect/load previous chat history for current session
- Configure `智慧财信机器人webhook地址` (outbound robot webhook URL)
- See the read-only `chasingclaw` inbound webhook URL for IM platform callback setup

## Webhook Protocol

Built-in webhook inbound endpoint (provided by `chasingclaw ui`):

- `POST /api/webhook/request`

Wisdom Caixin callback request example:

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

Wisdom Caixin-compatible callback response:

```json
{
  "result": "ok"
}
```

Outbound message format sent to `智慧财信机器人webhook地址`:

```json
{
  "msgtype": "text",
  "text": {
    "content": "...assistant response..."
  }
}
```

Notes:

- `智慧财信机器人webhook地址` is configured in UI and used as outbound robot send endpoint
- The UI displays a read-only inbound webhook URL for IM callback configuration (prefers LAN IP instead of localhost)
- You can override the displayed inbound host via env `CHASINGCLAW_PUBLIC_HOST`
- For local testing, `/api/webhook/request` still accepts `{ "message", "sessionId", "callbackUrl" }`

## Config Overview

Main config file:

- `~/.chasingclaw/config.json`

Common sections:

- `providers.*` for API keys/base URLs
- `agents.defaults.model` for default model
- `tools` for shell/web behavior
- `channels` for Telegram/Discord/Slack/Email/etc
- `channels.webhook` for webhook callback settings

## Docker

Container exposes:

- `18789` (Web UI)
- `18790` (Gateway)

## Security

Please see:

- `SECURITY.md`

## License

MIT
