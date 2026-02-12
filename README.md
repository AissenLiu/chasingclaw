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
- Configure `智慧财信机器人webhook地址` (outbound callback URL)
- See the read-only `chasingclaw` inbound webhook URL for IM platform callback setup

## Webhook Protocol

Built-in webhook endpoint (provided by `chasingclaw ui`):

- `POST /api/webhook/request`

Example request:

```json
{
  "message": "Summarize today's tasks",
  "sessionId": "team-a",
  "callbackUrl": "https://example.com/callback-hook"
}
```

Example response:

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

Notes:

- `callbackUrl` can be configured in UI (field name: `智慧财信机器人webhook地址`) or passed in request body
- The UI displays a read-only inbound webhook URL for IM callback configuration (prefers LAN IP instead of localhost)
- After generating reply, assistant posts callback payload to `callbackUrl`
- You can override the displayed inbound host via env `CHASINGCLAW_PUBLIC_HOST`

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
