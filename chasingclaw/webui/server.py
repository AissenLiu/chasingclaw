"""Minimal Web UI + webhook server for chasingclaw."""

from __future__ import annotations

import asyncio
import json
import os
import secrets
import socket
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx

from chasingclaw.agent.loop import AgentLoop
from chasingclaw.bus.queue import MessageBus
from chasingclaw.config.loader import load_config, save_config
from chasingclaw.providers.litellm_provider import LiteLLMProvider
from chasingclaw.providers.registry import PROVIDERS, find_by_name
from chasingclaw.session.manager import SessionManager


UI_HTML = """<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>chasingclaw UI</title>
  <style>
    body { font-family: -apple-system, BlinkMacSystemFont, Segoe UI, Roboto, sans-serif; margin: 0; background: #0f172a; color: #e2e8f0; }
    .wrap { max-width: 1100px; margin: 0 auto; padding: 16px; display: grid; grid-template-columns: 360px 1fr; gap: 16px; }
    .card { background: #111827; border: 1px solid #1f2937; border-radius: 10px; padding: 14px; }
    h1 { margin: 0 0 8px; font-size: 20px; }
    h2 { margin: 0 0 10px; font-size: 15px; }
    label { display: block; margin: 10px 0 5px; font-size: 12px; color: #94a3b8; }
    input, select, textarea, button {
      width: 100%; box-sizing: border-box; border-radius: 8px; border: 1px solid #334155;
      background: #0b1220; color: #e2e8f0; padding: 10px; font-size: 13px;
    }
    button { background: #2563eb; border: 0; cursor: pointer; font-weight: 600; margin-top: 8px; }
    button:hover { background: #1d4ed8; }
    .row { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }
    .chat-log { height: 70vh; overflow-y: auto; background: #020617; border-radius: 8px; padding: 12px; border: 1px solid #1e293b; }
    .msg { margin: 0 0 10px; padding: 8px 10px; border-radius: 8px; white-space: pre-wrap; }
    .user { background: #1e3a8a; }
    .bot { background: #064e3b; }
    .status { font-size: 12px; color: #94a3b8; margin-top: 8px; }
    .hint { font-size: 11px; color: #94a3b8; margin-top: 6px; }
  </style>
</head>
<body>
  <div class="wrap">
    <div class="card">
      <h1>chasingclaw</h1>
      <h2>模型与Webhook配置</h2>

      <label>Provider</label>
      <select id="provider"></select>

      <label>Model</label>
      <input id="model" placeholder="如 anthropic/claude-opus-4-5" />

      <label>API Base URL</label>
      <input id="apiBase" placeholder="可选，例如 http://localhost:8000/v1" />

      <label>API Key</label>
      <input id="apiKey" type="password" placeholder="输入后点击保存" />

      <label>智慧财信机器人webhook地址</label>
      <input id="webhookCallbackUrl" placeholder="例如 https://your-im-server/webhook" />

      <label>用于配置智慧财信机器人的webhook回调地址</label>
      <input id="inboundWebhookUrl" readonly />

      <button id="saveBtn">保存配置</button>
      <div class="status" id="cfgStatus"></div>
    </div>

    <div class="card">
      <h2>Chat</h2>
      <div class="status" id="sessionInfo"></div>
      <div class="chat-log" id="chatLog"></div>
      <div class="row" style="margin-top:10px;">
        <input id="message" placeholder="输入消息，Enter发送" />
        <button id="sendBtn">发送</button>
      </div>
      <div class="row" style="margin-top:8px;">
        <button id="newSessionBtn" style="background:#475569">新会话</button>
        <button id="webhookTestBtn" style="background:#0f766e">测试智慧财信发送</button>
      </div>
      <div class="status" id="chatStatus"></div>
    </div>
  </div>

<script>
const KEY = 'chasingclaw_webui_session_id';
let sessionId = localStorage.getItem(KEY) || crypto.randomUUID();
localStorage.setItem(KEY, sessionId);

const el = (id) => document.getElementById(id);
const chatLog = el('chatLog');

function appendMessage(role, text) {
  const div = document.createElement('div');
  div.className = 'msg ' + (role === 'user' ? 'user' : 'bot');
  div.textContent = text || '';
  chatLog.appendChild(div);
  chatLog.scrollTop = chatLog.scrollHeight;
}

async function api(path, options = {}) {
  const opts = { ...options };
  opts.headers = { 'Content-Type': 'application/json', ...(options.headers || {}) };
  const res = await fetch(path, opts);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new Error(data.error || ('HTTP ' + res.status));
  }
  return data;
}

function setSessionInfo() {
  el('sessionInfo').textContent = 'Session: ' + sessionId;
}

function isCustomProvider() {
  return el('provider').value === 'custom';
}

function updateApiBaseState() {
  const input = el('apiBase');
  const custom = isCustomProvider();
  input.disabled = !custom;
  if (!custom) {
    input.value = '';
    input.placeholder = '仅 custom 可填写';
  } else {
    input.placeholder = '例如 http://localhost:8000/v1';
  }
}

async function loadConfig() {
  const data = await api('/api/config');
  const provider = el('provider');
  provider.innerHTML = '';
  for (const p of data.providerOptions || []) {
    const option = document.createElement('option');
    option.value = p;
    option.textContent = p;
    provider.appendChild(option);
  }
  const providerValue = data.provider || '';
  if (![...provider.options].some((o) => o.value === providerValue) && providerValue) {
    const option = document.createElement('option');
    option.value = providerValue;
    option.textContent = providerValue;
    provider.appendChild(option);
  }
  provider.value = providerValue;
  updateApiBaseState();
  el('model').value = data.model || '';
  if (isCustomProvider()) {
    el('apiBase').value = data.apiBase || '';
  }
  el('apiKey').value = data.apiKey || '';
  el('webhookCallbackUrl').value = data.webhookCallbackUrl || '';
  el('inboundWebhookUrl').value = data.inboundWebhookUrl || '';
  el('cfgStatus').textContent = '配置已加载';
}

async function loadHistory() {
  chatLog.innerHTML = '';
  const data = await api('/api/history?sessionId=' + encodeURIComponent(sessionId));
  for (const item of data.messages || []) {
    if (item.role === 'user' || item.role === 'assistant') {
      appendMessage(item.role === 'user' ? 'user' : 'assistant', item.content || '');
    }
  }
}

async function saveConfig() {
  el('cfgStatus').textContent = '保存中...';
  const payload = {
    provider: el('provider').value,
    model: el('model').value,
    apiKey: el('apiKey').value,
    apiBase: isCustomProvider() ? el('apiBase').value : '',
    webhookCallbackUrl: el('webhookCallbackUrl').value,
  };
  await api('/api/config', {
    method: 'POST',
    body: JSON.stringify(payload),
  });
  el('cfgStatus').textContent = '保存成功';
}

async function sendChat() {
  const input = el('message');
  const message = input.value.trim();
  if (!message) return;
  input.value = '';
  appendMessage('user', message);
  el('chatStatus').textContent = '思考中...';
  try {
    const data = await api('/api/chat', {
      method: 'POST',
      body: JSON.stringify({ message, sessionId: sessionId })
    });
    appendMessage('assistant', data.reply || '');
    el('chatStatus').textContent = '';
  } catch (err) {
    appendMessage('assistant', 'Error: ' + err.message);
    el('chatStatus').textContent = '发送失败';
  }
}

async function testWebhook() {
  const input = el('message');
  const message = (input.value || 'chasingclaw 智慧财信联通测试').trim();
  el('chatStatus').textContent = '正在向智慧财信机器人发送测试消息...';
  try {
    const data = await api('/api/webhook/zhcx-test-send', {
      method: 'POST',
      body: JSON.stringify({ message }),
    });
    appendMessage('assistant', '[智慧财信测试] 已发送：' + message);
    if (data.result) {
      appendMessage('assistant', '[智慧财信测试返回] ' + JSON.stringify(data.result));
    }
    el('chatStatus').textContent = '智慧财信测试发送完成';
  } catch (err) {
    appendMessage('assistant', '智慧财信测试失败: ' + err.message);
    el('chatStatus').textContent = '智慧财信测试失败';
  }
}

el('saveBtn').addEventListener('click', saveConfig);
el('provider').addEventListener('change', updateApiBaseState);
el('sendBtn').addEventListener('click', sendChat);
el('webhookTestBtn').addEventListener('click', testWebhook);
el('newSessionBtn').addEventListener('click', async () => {
  sessionId = crypto.randomUUID();
  localStorage.setItem(KEY, sessionId);
  setSessionInfo();
  await loadHistory();
});
el('message').addEventListener('keydown', (e) => {
  if (e.key === 'Enter') sendChat();
});

(async () => {
  setSessionInfo();
  await loadConfig();
  await loadHistory();
})();
</script>
</body>
</html>
"""


class WebUIRuntime:
    """Runtime service used by the HTTP handlers."""

    def __init__(self, host: str, port: int):
        self.host = host
        self.port = port
        self._lock = threading.Lock()

    def _detect_lan_ip(self) -> str | None:
        for target in ("8.8.8.8", "1.1.1.1"):
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                    sock.connect((target, 80))
                    ip = sock.getsockname()[0]
                    if ip and not ip.startswith("127."):
                        return ip
            except OSError:
                continue

        try:
            ip = socket.gethostbyname(socket.gethostname())
            if ip and not ip.startswith("127."):
                return ip
        except OSError:
            pass

        return None

    def _public_host(self) -> str:
        override = os.environ.get("CHASINGCLAW_PUBLIC_HOST", "").strip()
        if override:
            return override

        # For wildcard/loopback binding, expose a LAN-reachable inbound URL for webhook setup.
        if self.host in {"0.0.0.0", "::", "localhost", "127.0.0.1", "::1"}:
            return self._detect_lan_ip() or "127.0.0.1"

        return self.host

    @property
    def inbound_webhook_url(self) -> str:
        return f"http://{self._public_host()}:{self.port}/api/webhook/request"

    def _provider_options(self) -> list[str]:
        return [spec.name for spec in PROVIDERS] + ["custom"]

    def _make_provider(self, config: Any) -> LiteLLMProvider:
        model = config.agents.defaults.model
        selected = (config.ui.selected_provider or "").strip().lower()

        # Custom mode: use OpenAI-compatible endpoint provided by user
        if selected == "custom":
            provider_cfg = config.providers.openai
            if not (provider_cfg and provider_cfg.api_key) and not model.startswith("bedrock/"):
                raise ValueError("Custom provider requires API key.")
            return LiteLLMProvider(
                api_key=provider_cfg.api_key if provider_cfg else None,
                api_base=provider_cfg.api_base if provider_cfg else None,
                default_model=model,
                extra_headers=provider_cfg.extra_headers if provider_cfg else None,
                provider_name=None,
            )

        if selected and selected != "custom" and hasattr(config.providers, selected):
            provider_cfg = getattr(config.providers, selected)
            if not (provider_cfg and provider_cfg.api_key) and not model.startswith("bedrock/"):
                raise ValueError("No API key configured for selected provider.")

            api_base = provider_cfg.api_base if provider_cfg else None
            spec = find_by_name(selected)
            if not api_base and spec and spec.is_gateway and spec.default_api_base:
                api_base = spec.default_api_base

            return LiteLLMProvider(
                api_key=provider_cfg.api_key if provider_cfg else None,
                api_base=api_base,
                default_model=model,
                extra_headers=provider_cfg.extra_headers if provider_cfg else None,
                provider_name=selected,
            )

        provider_cfg = config.get_provider()
        if not (provider_cfg and provider_cfg.api_key) and not model.startswith("bedrock/"):
            raise ValueError("No API key configured. Please save provider/apiKey in the UI first.")
        return LiteLLMProvider(
            api_key=provider_cfg.api_key if provider_cfg else None,
            api_base=config.get_api_base(),
            default_model=model,
            extra_headers=provider_cfg.extra_headers if provider_cfg else None,
            provider_name=config.get_provider_name(),
        )

    def load_ui_config(self) -> dict[str, Any]:
        config = load_config()
        model = config.agents.defaults.model

        selected = (config.ui.selected_provider or "").strip().lower()
        if selected not in self._provider_options():
            selected = config.get_provider_name(model) or "openrouter"

        if selected == "custom":
            provider_cfg = config.providers.openai
        else:
            provider_cfg = getattr(config.providers, selected, None)

        webhook_cfg = config.channels.webhook
        return {
            "provider": selected,
            "providerOptions": self._provider_options(),
            "model": model,
            "apiKey": provider_cfg.api_key if provider_cfg else "",
            "apiBase": provider_cfg.api_base if provider_cfg and selected == "custom" else "",
            "webhookCallbackUrl": webhook_cfg.callback_url,
            "inboundWebhookUrl": self.inbound_webhook_url,
        }

    def save_ui_config(self, payload: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            config = load_config()

            provider_name = str(payload.get("provider") or "").strip().lower()
            options = set(self._provider_options())
            if provider_name and provider_name not in options:
                raise ValueError(f"Unknown provider: {provider_name}")
            if not provider_name:
                provider_name = (config.ui.selected_provider or config.get_provider_name() or "openrouter").lower()

            config.ui.selected_provider = provider_name

            if provider_name == "custom":
                provider_cfg = config.providers.openai
                if "apiBase" in payload:
                    api_base = str(payload.get("apiBase") or "").strip()
                    provider_cfg.api_base = api_base or None
            else:
                provider_cfg = getattr(config.providers, provider_name)

            if "apiKey" in payload:
                provider_cfg.api_key = str(payload.get("apiKey") or "").strip()

            if "model" in payload:
                model = str(payload.get("model") or "").strip()
                if model:
                    config.agents.defaults.model = model

            webhook = config.channels.webhook
            if "webhookCallbackUrl" in payload:
                webhook.callback_url = str(payload.get("webhookCallbackUrl") or "").strip()
            webhook.enabled = bool(webhook.callback_url)

            save_config(config)

        return self.load_ui_config()

    async def _chat_once_async(self, message: str, session_id: str, channel: str) -> str:
        config = load_config()
        provider = self._make_provider(config)
        bus = MessageBus()
        session_manager = SessionManager(config.workspace_path)

        agent = AgentLoop(
            bus=bus,
            provider=provider,
            workspace=config.workspace_path,
            model=config.agents.defaults.model,
            max_iterations=config.agents.defaults.max_tool_iterations,
            brave_api_key=config.tools.web.search.api_key or None,
            exec_config=config.tools.exec,
            restrict_to_workspace=config.tools.restrict_to_workspace,
            session_manager=session_manager,
        )
        return await agent.process_direct(
            content=message,
            session_key=f"{channel}:{session_id}",
            channel=channel,
            chat_id=session_id,
        )

    def chat(self, payload: dict[str, Any], channel: str = "webui") -> dict[str, Any]:
        message = str(payload.get("message") or "").strip()
        if not message:
            raise ValueError("message is required")

        session_id = str(payload.get("sessionId") or secrets.token_hex(8)).strip()
        reply = asyncio.run(self._chat_once_async(message, session_id, channel))
        history = self.get_history(session_id, channel)

        return {
            "sessionId": session_id,
            "reply": reply,
            "history": history,
        }

    def get_history(self, session_id: str, channel: str = "webui") -> list[dict[str, Any]]:
        config = load_config()
        session_manager = SessionManager(config.workspace_path)
        session = session_manager.get_or_create(f"{channel}:{session_id}")
        messages: list[dict[str, Any]] = []
        for item in session.messages[-100:]:
            messages.append(
                {
                    "role": item.get("role", "assistant"),
                    "content": item.get("content", ""),
                    "timestamp": item.get("timestamp", ""),
                }
            )
        return messages

    def _post_json(self, url: str, payload: dict[str, Any], timeout: int) -> dict[str, Any]:
        try:
            with httpx.Client(timeout=timeout) as client:
                response = client.post(url, json=payload)
                body: Any
                ctype = response.headers.get("content-type", "")
                if "application/json" in ctype:
                    body = response.json()
                else:
                    body = response.text[:1000]
                return {
                    "ok": response.is_success,
                    "status": response.status_code,
                    "body": body,
                }
        except Exception as exc:  # pragma: no cover - network failures vary by env
            return {
                "ok": False,
                "error": str(exc),
            }

    def _is_zhcx_callback_payload(self, payload: dict[str, Any]) -> bool:
        return "chatid" in payload and "content" in payload

    def _build_zhcx_text_payload(self, reply: str) -> dict[str, Any]:
        # Wisdom Caixin webhook: single message <= 5000 characters.
        content = (reply or "").strip() or " "
        if len(content) > 5000:
            content = content[:5000]
        return {
            "msgtype": "text",
            "text": {
                "content": content,
            },
        }

    def send_zhcx_test_message(self, payload: dict[str, Any]) -> dict[str, Any]:
        config = load_config()
        webhook = config.channels.webhook
        callback_url = str(webhook.callback_url or "").strip()
        if not callback_url:
            raise ValueError("请先在前端配置智慧财信机器人webhook地址")

        timeout = max(1, int(webhook.timeout_seconds))
        message = str(payload.get("message") or "").strip() or "chasingclaw 智慧财信联通测试"
        outbound_payload = self._build_zhcx_text_payload(message)
        result = self._post_json(callback_url, outbound_payload, timeout=timeout)
        return {
            "ok": bool(result.get("ok")),
            "url": callback_url,
            "payload": outbound_payload,
            "result": result,
        }

    def handle_webhook(self, payload: dict[str, Any]) -> dict[str, Any]:
        is_zhcx_payload = self._is_zhcx_callback_payload(payload)
        message = str(payload.get("message") or payload.get("content") or "").strip()
        if not message:
            if is_zhcx_payload:
                # Wisdom Caixin callback endpoint expects a strict ack payload.
                return {"result": "ok"}
            raise ValueError("message is required")

        raw_session_id = payload.get("sessionId")
        if raw_session_id in {None, ""}:
            raw_session_id = payload.get("chatid")
        session_id = str(raw_session_id or secrets.token_hex(8)).strip()

        config = load_config()
        webhook = config.channels.webhook
        timeout = max(1, int(webhook.timeout_seconds))

        callback_url = str(payload.get("callbackUrl") or webhook.callback_url or "").strip()
        current_request_url = str(payload.get("_currentRequestUrl") or "").strip()

        chat_result = self.chat(
            {
                "message": message,
                "sessionId": session_id,
            },
            channel="webhook",
        )

        reply_text = str(chat_result.get("reply", ""))
        callback_payload: dict[str, Any]
        if is_zhcx_payload:
            callback_payload = self._build_zhcx_text_payload(reply_text)
        else:
            callback_payload = {
                "type": "chasingclaw.webhook.callback",
                "sessionId": session_id,
                "message": message,
                "reply": reply_text,
            }

        callback_result: dict[str, Any] | None = None
        if callback_url:
            if callback_url == current_request_url:
                callback_result = {
                    "ok": False,
                    "error": "callback_url cannot be the same as current request endpoint",
                }
            else:
                callback_result = self._post_json(callback_url, callback_payload, timeout=timeout)

        if is_zhcx_payload:
            # Keep the callback response compatible with Wisdom Caixin protocol.
            return {"result": "ok"}

        return {
            "ok": True,
            "sessionId": session_id,
            "reply": reply_text,
            "callback": callback_result,
        }


class _WebUIHandler(BaseHTTPRequestHandler):
    """HTTP handler bound to a WebUIRuntime instance."""

    runtime: WebUIRuntime

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A003
        return

    def _send_json(self, status: int, payload: dict[str, Any]) -> None:
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _send_html(self, html: str) -> None:
        raw = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _read_json(self) -> dict[str, Any]:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        if not raw:
            return {}
        try:
            data = json.loads(raw.decode("utf-8"))
            return data if isinstance(data, dict) else {}
        except json.JSONDecodeError:
            raise ValueError("invalid JSON body")

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)

        if parsed.path == "/":
            self._send_html(UI_HTML)
            return

        if parsed.path == "/healthz":
            self._send_json(200, {"ok": True})
            return

        if parsed.path == "/api/config":
            try:
                self._send_json(200, self.runtime.load_ui_config())
            except Exception as exc:
                self._send_json(500, {"error": str(exc)})
            return

        if parsed.path == "/api/history":
            query = parse_qs(parsed.query)
            session_id = (query.get("sessionId") or [""])[0].strip()
            channel = (query.get("channel") or ["webui"])[0].strip() or "webui"
            if not session_id:
                self._send_json(400, {"error": "sessionId is required"})
                return
            try:
                self._send_json(200, {"messages": self.runtime.get_history(session_id, channel=channel)})
            except Exception as exc:
                self._send_json(500, {"error": str(exc)})
            return

        if parsed.path == "/api/webhook/request":
            # Wisdom Caixin callback availability check expects this exact payload.
            self._send_json(200, {"result": "ok"})
            return

        self._send_json(404, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        try:
            payload = self._read_json()
        except ValueError as exc:
            self._send_json(400, {"error": str(exc)})
            return

        try:
            if parsed.path == "/api/config":
                updated = self.runtime.save_ui_config(payload)
                self._send_json(200, updated)
                return

            if parsed.path == "/api/chat":
                result = self.runtime.chat(payload, channel="webui")
                self._send_json(200, result)
                return

            if parsed.path == "/api/webhook/zhcx-test-send":
                result = self.runtime.send_zhcx_test_message(payload)
                self._send_json(200, result)
                return

            if parsed.path == "/api/webhook/request":
                host = self.headers.get("Host") or f"{self.runtime._public_host()}:{self.runtime.port}"
                payload["_currentRequestUrl"] = f"http://{host}{parsed.path}"
                result = self.runtime.handle_webhook(payload)
                self._send_json(200, result)
                return

            self._send_json(404, {"error": "not found"})
        except ValueError as exc:
            self._send_json(400, {"error": str(exc)})
        except Exception as exc:
            self._send_json(500, {"error": str(exc)})


class WebUIServer:
    """Threading HTTP server that exposes Web UI and webhook endpoints."""

    def __init__(self, host: str = "0.0.0.0", port: int = 18789):
        self.host = host
        self.port = port
        self.runtime = WebUIRuntime(host, port)
        self._httpd: ThreadingHTTPServer | None = None

    def serve(self, open_browser: bool = False) -> None:
        """Start serving forever until KeyboardInterrupt."""

        handler_cls = type(
            "BoundWebUIHandler",
            (_WebUIHandler,),
            {"runtime": self.runtime},
        )
        self._httpd = ThreadingHTTPServer((self.host, self.port), handler_cls)

        if open_browser:
            webbrowser.open(f"http://{self.runtime._public_host()}:{self.port}")

        try:
            self._httpd.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            self.close()

    def close(self) -> None:
        if self._httpd:
            self._httpd.shutdown()
            self._httpd.server_close()
            self._httpd = None
