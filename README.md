# chasingclaw

中文文档（Chinese doc）: [README.zh-CN.md](./README.zh-CN.md)

`chasingclaw` is an ultra-lightweight personal AI assistant framework.

## What's New in This Version

- Project has been fully renamed from `nanobot` to `chasingclaw`
- Removed logo/case media assets and demo image bundles
- Added Web UI server (default port `18789`)
- Added configurable webhook request/callback support

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
- Continue conversation by persistent session id in browser storage
- Inspect/load previous chat history for current session
- Configure webhook request/callback URLs

## Webhook Protocol

Built-in webhook endpoint (provided by `chasingclaw ui`):

- `POST /api/webhook/request`

Example request:

```json
{
  "message": "Summarize today's tasks",
  "sessionId": "team-a",
  "requestUrl": "https://example.com/request-hook",
  "callbackUrl": "https://example.com/callback-hook"
}
```

Example response:

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

Notes:

- `requestUrl` and `callbackUrl` can also be configured in UI and persisted to config
- If configured, assistant will relay request payload to `requestUrl`
- After generating reply, assistant posts callback payload to `callbackUrl`

## Config Overview

Main config file:

- `~/.chasingclaw/config.json`

Common sections:

- `providers.*` for API keys/base URLs
- `agents.defaults.model` for default model
- `tools` for shell/web behavior
- `channels` for Telegram/Discord/Slack/Email/etc
- `channels.webhook` for webhook request/callback settings

## Docker

Container exposes:

- `18789` (Web UI)
- `18790` (Gateway)

## Security

Please see:

- `SECURITY.md`

## License

MIT
