"""Minimal Web UI + webhook server for chasingclaw."""

from __future__ import annotations

import asyncio
import datetime
import hashlib
import json
import os
from pathlib import Path
import secrets
import socket
import threading
import traceback
import webbrowser
from email.utils import formatdate
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx
from loguru import logger

from chasingclaw.agent.loop import AgentLoop
from chasingclaw.bus.queue import MessageBus
from chasingclaw.config.loader import load_config, save_config
from chasingclaw.providers.litellm_provider import LiteLLMProvider
from chasingclaw.providers.registry import PROVIDERS, find_by_name
from chasingclaw.session.manager import SessionManager


UI_HTML = (Path(__file__).with_name("ui.html")).read_text(encoding="utf-8")


class WebUIRuntime:
    """Runtime service used by the HTTP handlers."""

    def __init__(self, host: str, port: int):
        self.host = host
        self.port = port
        self._lock = threading.Lock()
        self._webhook_events: list[dict[str, Any]] = []
        self._webhook_event_seq = 0
        self._webhook_event_limit = 300

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
        return [spec.name for spec in PROVIDERS] + ["custom", "intranet"]

    def _make_provider(self, config: Any) -> LiteLLMProvider:
        model = config.agents.defaults.model
        selected = (config.ui.selected_provider or "").strip().lower()

        # Custom mode: keep legacy routing behavior, use user-provided API base as-is.
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

        # Intranet mode: force OpenAI-compatible routing for internal endpoints.
        if selected == "intranet":
            provider_cfg = config.providers.openai
            if not (provider_cfg and provider_cfg.api_key) and not model.startswith("bedrock/"):
                raise ValueError("Intranet provider requires API key.")
            return LiteLLMProvider(
                api_key=provider_cfg.api_key if provider_cfg else None,
                api_base=provider_cfg.api_base if provider_cfg else None,
                default_model=model,
                extra_headers=provider_cfg.extra_headers if provider_cfg else None,
                provider_name="openai",
            )

        if selected and selected not in {"custom", "intranet"} and hasattr(config.providers, selected):
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

    def _as_bool(self, value: Any) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return bool(value)
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "on"}
        return False

    def _format_now(self) -> str:
        return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")

    def _sanitize_for_debug(self, value: Any, depth: int = 0) -> Any:
        if depth > 4:
            return "..."

        if isinstance(value, dict):
            sanitized: dict[str, Any] = {}
            for key, raw in value.items():
                name = str(key)
                lowered = name.lower()
                if lowered in {"authorization", "api_key", "apikey", "secret", "sign_secret", "webhooksignsecret"}:
                    sanitized[name] = "***"
                else:
                    sanitized[name] = self._sanitize_for_debug(raw, depth + 1)
            return sanitized

        if isinstance(value, list):
            return [self._sanitize_for_debug(item, depth + 1) for item in value[:20]]

        if isinstance(value, str):
            text = value.strip()
            if len(text) > 1200:
                return text[:1200] + "...(truncated)"
            return text

        return value

    def _clip(self, text: str, limit: int = 220) -> str:
        clean = (text or "").strip().replace("\n", " ")
        if len(clean) <= limit:
            return clean
        return clean[:limit] + "...(truncated)"

    def record_webhook_event(
        self,
        event: str,
        summary: str,
        *,
        session_id: str = "",
        detail: Any | None = None,
        level: str = "info",
    ) -> None:
        payload: dict[str, Any] = {
            "id": 0,
            "timestamp": self._format_now(),
            "event": event,
            "summary": summary,
            "sessionId": session_id or "",
        }
        if detail is not None:
            payload["detail"] = self._sanitize_for_debug(detail)

        with self._lock:
            self._webhook_event_seq += 1
            payload["id"] = self._webhook_event_seq
            self._webhook_events.append(payload)
            if len(self._webhook_events) > self._webhook_event_limit:
                self._webhook_events = self._webhook_events[-self._webhook_event_limit :]

        log_line = f"webhook[{event}] sid={session_id or '-'} {summary}"
        if "detail" in payload:
            detail_text = json.dumps(payload["detail"], ensure_ascii=False)
            if len(detail_text) > 1200:
                detail_text = detail_text[:1200] + "...(truncated)"
            log_line = f"{log_line} | detail={detail_text}"

        if level == "error":
            logger.error(log_line)
        elif level == "warning":
            logger.warning(log_line)
        else:
            logger.info(log_line)

    def list_webhook_events(self, since_id: int = 0, limit: int = 80) -> dict[str, Any]:
        if limit <= 0:
            limit = 1
        limit = min(limit, 300)

        with self._lock:
            if since_id > 0:
                events = [item for item in self._webhook_events if int(item.get("id", 0)) > since_id]
                if len(events) > limit:
                    events = events[-limit:]
            else:
                events = self._webhook_events[-limit:]
            last_id = self._webhook_event_seq

        return {
            "events": events,
            "lastId": last_id,
        }

    def load_ui_config(self) -> dict[str, Any]:
        config = load_config()
        model = config.agents.defaults.model

        selected = (config.ui.selected_provider or "").strip().lower()
        if selected not in self._provider_options():
            selected = config.get_provider_name(model) or "openrouter"

        if selected in {"custom", "intranet"}:
            provider_cfg = config.providers.openai
        else:
            provider_cfg = getattr(config.providers, selected, None)

        webhook_cfg = config.channels.webhook
        return {
            "provider": selected,
            "providerOptions": self._provider_options(),
            "model": model,
            "apiKey": provider_cfg.api_key if provider_cfg else "",
            "apiBase": provider_cfg.api_base if provider_cfg and selected in {"custom", "intranet"} else "",
            "restrictToWorkspace": bool(config.tools.restrict_to_workspace),
            "webhookCallbackUrl": webhook_cfg.callback_url,
            "webhookTimeoutSeconds": webhook_cfg.timeout_seconds,
            "webhookSignKey": webhook_cfg.sign_key,
            "webhookSignSecret": webhook_cfg.sign_secret,
            "webhookMessageType": webhook_cfg.message_type,
            "webhookMessageTemplate": webhook_cfg.message_template,
            "webhookLinkTitle": webhook_cfg.link_title,
            "webhookLinkMessageUrl": webhook_cfg.link_message_url,
            "webhookLinkButtonTitle": webhook_cfg.link_button_title,
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

            if provider_name in {"custom", "intranet"}:
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

            if "restrictToWorkspace" in payload:
                config.tools.restrict_to_workspace = self._as_bool(payload.get("restrictToWorkspace"))

            webhook = config.channels.webhook
            if "webhookCallbackUrl" in payload:
                webhook.callback_url = str(payload.get("webhookCallbackUrl") or "").strip()
            if "webhookTimeoutSeconds" in payload:
                try:
                    webhook.timeout_seconds = max(1, int(payload.get("webhookTimeoutSeconds") or 15))
                except (TypeError, ValueError):
                    webhook.timeout_seconds = 15

            if "webhookSignKey" in payload:
                webhook.sign_key = str(payload.get("webhookSignKey") or "").strip()
            if "webhookSignSecret" in payload:
                webhook.sign_secret = str(payload.get("webhookSignSecret") or "").strip()

            if "webhookMessageType" in payload:
                message_type = str(payload.get("webhookMessageType") or "text").strip().lower()
                if message_type not in {"text", "markdown", "link"}:
                    raise ValueError("webhookMessageType must be one of: text, markdown, link")
                webhook.message_type = message_type

            if "webhookMessageTemplate" in payload:
                webhook.message_template = str(payload.get("webhookMessageTemplate") or "").strip() or "{reply}"

            if "webhookLinkTitle" in payload:
                webhook.link_title = str(payload.get("webhookLinkTitle") or "").strip() or "chasingclaw 回复"
            if "webhookLinkMessageUrl" in payload:
                webhook.link_message_url = str(payload.get("webhookLinkMessageUrl") or "").strip()
            if "webhookLinkButtonTitle" in payload:
                webhook.link_button_title = str(payload.get("webhookLinkButtonTitle") or "").strip() or "查看详情"

            webhook.enabled = bool(webhook.callback_url)

            save_config(config)

        return self.load_ui_config()

    async def _chat_once_async(
        self,
        message: str,
        session_id: str,
        channel: str,
        *,
        display_message: str | None = None,
        attachments: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
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
        metadata: dict[str, Any] = {}
        if display_message:
            metadata["displayContent"] = display_message
        if attachments:
            metadata["attachments"] = attachments

        outbound = await agent.process_direct_with_result(
            content=message,
            session_key=f"{channel}:{session_id}",
            channel=channel,
            chat_id=session_id,
            metadata=metadata,
        )
        reply = outbound.content if outbound else ""
        trace = []
        if outbound and isinstance(outbound.metadata, dict):
            raw_trace = outbound.metadata.get("trace")
            if isinstance(raw_trace, list):
                trace = raw_trace
        return {
            "reply": reply,
            "trace": trace,
        }

    def chat(self, payload: dict[str, Any], channel: str = "webui") -> dict[str, Any]:
        message = str(payload.get("message") or "").strip()
        if not message:
            raise ValueError("message is required")

        session_id = str(payload.get("sessionId") or secrets.token_hex(8)).strip()
        display_message = str(payload.get("displayMessage") or "").strip() or None
        raw_attachments = payload.get("attachments")
        attachments: list[dict[str, Any]] = []
        if isinstance(raw_attachments, list):
            for item in raw_attachments[:10]:
                if not isinstance(item, dict):
                    continue
                name = str(item.get("name") or "").strip()
                size = item.get("size")
                ftype = str(item.get("type") or "").strip()
                if not name:
                    continue
                clean_item: dict[str, Any] = {"name": name}
                if isinstance(size, int):
                    clean_item["size"] = size
                elif isinstance(size, float):
                    clean_item["size"] = int(size)
                if ftype:
                    clean_item["type"] = ftype
                attachments.append(clean_item)

        run_result = asyncio.run(
            self._chat_once_async(
                message,
                session_id,
                channel,
                display_message=display_message,
                attachments=attachments or None,
            )
        )
        history = self.get_history(session_id, channel)
        reply = str(run_result.get("reply") or "")
        trace = run_result.get("trace") if isinstance(run_result, dict) else []

        return {
            "sessionId": session_id,
            "reply": reply,
            "history": history,
            "trace": trace if isinstance(trace, list) else [],
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
                    "trace": item.get("trace", []) if isinstance(item.get("trace"), list) else [],
                    "attachments": item.get("attachments", []) if isinstance(item.get("attachments"), list) else [],
                }
            )
        return messages

    def list_sessions(self, channel: str = "webui", limit: int = 200) -> dict[str, Any]:
        config = load_config()
        session_manager = SessionManager(config.workspace_path)
        sessions = session_manager.list_sessions()
        items: list[dict[str, Any]] = []
        prefix = f"{channel}:"

        for meta in sessions:
            key = str(meta.get("key") or "")
            if not key.startswith(prefix):
                continue

            session_id = key.split(":", 1)[1] if ":" in key else key
            session = session_manager.get_or_create(key)

            title = ""
            for msg in session.messages:
                if msg.get("role") != "user":
                    continue
                content = str(msg.get("content") or "").strip()
                if content:
                    title = self._clip(content, limit=60)
                    break
            if not title:
                title = f"会话 {session_id[:8]}"

            preview = ""
            if session.messages:
                preview = self._clip(str(session.messages[-1].get("content") or ""), limit=90)

            updated_at = str(meta.get("updated_at") or "")
            items.append(
                {
                    "sessionId": session_id,
                    "key": key,
                    "title": title,
                    "preview": preview,
                    "updatedAt": updated_at,
                    "messageCount": len(session.messages),
                }
            )
            if len(items) >= limit:
                break

        items.sort(key=lambda x: x.get("updatedAt") or "", reverse=True)
        return {"sessions": items}

    def remove_session(self, session_id: str, channel: str = "webui") -> dict[str, Any]:
        config = load_config()
        session_manager = SessionManager(config.workspace_path)
        key = f"{channel}:{session_id}"
        deleted = session_manager.delete(key)
        return {"success": deleted, "sessionId": session_id}

    def _post_json(
        self,
        url: str,
        payload: dict[str, Any],
        timeout: int,
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        raw_body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        req_headers = {"Content-Type": "application/json"}
        if headers:
            req_headers.update(headers)

        try:
            with httpx.Client(timeout=timeout) as client:
                response = client.post(url, content=raw_body, headers=req_headers)
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

    def _render_message(self, reply: str, template: str) -> str:
        tmpl = (template or "").strip()
        if not tmpl:
            return reply or ""
        if "{reply}" in tmpl:
            return tmpl.replace("{reply}", reply or "")
        return f"{tmpl}\n{reply}" if reply else tmpl

    def _truncate(self, text: str, limit: int = 5000) -> str:
        clean = (text or "").strip() or " "
        return clean[:limit]

    def _build_zhcx_payload_from_text(self, text: str, message_type: str, webhook: Any) -> dict[str, Any]:
        message_type = (message_type or "text").strip().lower()
        rendered = self._truncate(text)

        if message_type == "markdown":
            return {
                "msgtype": "markdown",
                "markdown": {
                    "text": rendered,
                },
            }

        if message_type == "link":
            title = (webhook.link_title or "chasingclaw 回复").strip() or "chasingclaw 回复"
            btn_title = (webhook.link_button_title or "查看详情").strip() or "查看详情"
            link_obj: dict[str, Any] = {
                "title": title,
                "text": rendered,
                "btnTitle": btn_title[:12],
            }
            link_url = (webhook.link_message_url or "").strip()
            if link_url:
                link_obj["messageUrl"] = link_url
            return {
                "msgtype": "link",
                "link": link_obj,
            }

        return {
            "msgtype": "text",
            "text": {
                "content": rendered,
            },
        }

    def _build_zhcx_outbound_payload(self, reply: str, webhook: Any) -> dict[str, Any]:
        rendered = self._render_message(reply or "", webhook.message_template)
        return self._build_zhcx_payload_from_text(rendered, webhook.message_type, webhook)

    def _build_sign_headers(self, body_bytes: bytes, sign_key: str, sign_secret: str) -> dict[str, str]:
        key = (sign_key or "").strip()
        secret = (sign_secret or "").strip()
        if not key or not secret:
            return {}

        content_md5 = hashlib.md5(body_bytes).hexdigest()
        date_gmt = formatdate(usegmt=True)
        sign_text = f"{secret}{content_md5}application/json{date_gmt}"
        signature = hashlib.sha1(sign_text.encode("utf-8")).hexdigest()

        return {
            "Content-Md5": content_md5,
            "Content-Type": "application/json",
            "DATE": date_gmt,
            "Authorization": f"{key}:{signature}",
        }

    def _post_zhcx_payload(self, url: str, payload: dict[str, Any], timeout: int, webhook: Any) -> dict[str, Any]:
        body_bytes = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        headers = self._build_sign_headers(body_bytes, webhook.sign_key, webhook.sign_secret)
        return self._post_json(url, payload, timeout=timeout, headers=headers)

    def send_zhcx_test_message(self, payload: dict[str, Any]) -> dict[str, Any]:
        config = load_config()
        webhook = config.channels.webhook
        callback_url = str(webhook.callback_url or "").strip()
        if not callback_url:
            raise ValueError("请先在前端配置智慧财信机器人webhook地址")

        timeout = max(1, int(webhook.timeout_seconds))
        message = str(payload.get("message") or "").strip() or "chasingclaw 智慧财信联通测试"
        outbound_payload = self._build_zhcx_payload_from_text(message, webhook.message_type, webhook)
        self.record_webhook_event(
            "test_send",
            f"前端触发测试发送 -> {callback_url}",
            detail={"payload": outbound_payload},
        )
        result = self._post_zhcx_payload(callback_url, outbound_payload, timeout=timeout, webhook=webhook)
        ok = bool(result.get("ok"))
        self.record_webhook_event(
            "test_result",
            f"测试发送{'成功' if ok else '失败'}",
            detail=result,
            level="info" if ok else "warning",
        )
        return {
            "ok": ok,
            "url": callback_url,
            "payload": outbound_payload,
            "result": result,
        }

    def handle_webhook(self, payload: dict[str, Any]) -> dict[str, Any]:
        is_zhcx_payload = self._is_zhcx_callback_payload(payload)
        raw_session_id = payload.get("sessionId")
        if raw_session_id in {None, ""}:
            raw_session_id = payload.get("chatid")
        session_id = str(raw_session_id or secrets.token_hex(8)).strip()

        message = str(payload.get("message") or payload.get("content") or "").strip()
        chat_id = str(payload.get("chatid") or "").strip()
        source = "智慧财信" if is_zhcx_payload else "通用"

        self.record_webhook_event(
            "inbound",
            f"收到{source}入站消息 chatid={chat_id or '-'} content={self._clip(message or '<empty>', 180)}",
            session_id=session_id,
            detail={"payload": payload},
        )

        if not message:
            if is_zhcx_payload:
                self.record_webhook_event(
                    "ack_only",
                    "消息内容为空，按智慧财信协议返回 {'result':'ok'}",
                    session_id=session_id,
                    level="warning",
                )
                return {"result": "ok"}
            raise ValueError("message is required")

        config = load_config()
        webhook = config.channels.webhook
        timeout = max(1, int(webhook.timeout_seconds))

        callback_url = str(payload.get("callbackUrl") or webhook.callback_url or "").strip()
        current_request_url = str(payload.get("_currentRequestUrl") or "").strip()

        if callback_url:
            self.record_webhook_event(
                "callback_target",
                f"准备回调到: {callback_url}",
                session_id=session_id,
            )
        else:
            self.record_webhook_event(
                "callback_target",
                "未配置回调地址，仅处理入站消息不回推",
                session_id=session_id,
                level="warning",
            )

        chat_result = self.chat(
            {
                "message": message,
                "sessionId": session_id,
            },
            channel="webhook",
        )

        reply_text = str(chat_result.get("reply", ""))
        self.record_webhook_event(
            "agent_reply",
            f"AI 回复: {self._clip(reply_text, 220)}",
            session_id=session_id,
        )

        callback_payload: dict[str, Any]
        if is_zhcx_payload:
            callback_payload = self._build_zhcx_outbound_payload(reply_text, webhook)
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
                self.record_webhook_event(
                    "callback_result",
                    "回调地址与入站地址相同，已跳过发送",
                    session_id=session_id,
                    detail=callback_result,
                    level="warning",
                )
            else:
                self.record_webhook_event(
                    "callback_send",
                    f"开始发送回调 -> {callback_url}",
                    session_id=session_id,
                    detail={"payload": callback_payload},
                )
                if is_zhcx_payload:
                    callback_result = self._post_zhcx_payload(callback_url, callback_payload, timeout=timeout, webhook=webhook)
                else:
                    callback_result = self._post_json(callback_url, callback_payload, timeout=timeout)

                ok = bool(callback_result.get("ok"))
                status = callback_result.get("status")
                if status is None:
                    summary = f"回调发送{'成功' if ok else '失败'}"
                else:
                    summary = f"回调发送{'成功' if ok else '失败'} status={status}"
                self.record_webhook_event(
                    "callback_result",
                    summary,
                    session_id=session_id,
                    detail=callback_result,
                    level="info" if ok else "warning",
                )

        if is_zhcx_payload:
            self.record_webhook_event(
                "ack_response",
                "已向智慧财信返回 {'result':'ok'}",
                session_id=session_id,
            )
            # Keep the callback response compatible with Wisdom Caixin protocol.
            return {"result": "ok"}

        return {
            "ok": True,
            "sessionId": session_id,
            "reply": reply_text,
            "callback": callback_result,
        }

    def _cron_service(self):
        from chasingclaw.config.loader import get_data_dir
        from chasingclaw.cron.service import CronService

        store_path = get_data_dir() / "cron" / "jobs.json"
        return CronService(store_path)

    def _cron_job_to_dict(self, job: Any) -> dict[str, Any]:
        schedule_kind = job.schedule.kind
        at_iso = ""
        if job.schedule.at_ms:
            at_iso = datetime.datetime.fromtimestamp(job.schedule.at_ms / 1000).isoformat(timespec="minutes")

        return {
            "id": job.id,
            "name": job.name,
            "enabled": job.enabled,
            "scheduleKind": schedule_kind,
            "everySeconds": (job.schedule.every_ms or 0) // 1000,
            "cronExpr": job.schedule.expr or "",
            "atIso": at_iso,
            "message": job.payload.message,
            "nextRunAtMs": job.state.next_run_at_ms,
            "lastRunAtMs": job.state.last_run_at_ms,
            "lastStatus": job.state.last_status,
            "lastError": job.state.last_error,
        }

    def list_cron_jobs(self, include_disabled: bool = True) -> dict[str, Any]:
        service = self._cron_service()
        jobs = service.list_jobs(include_disabled=include_disabled)
        return {
            "jobs": [self._cron_job_to_dict(job) for job in jobs],
        }

    def add_cron_job(self, payload: dict[str, Any]) -> dict[str, Any]:
        from chasingclaw.cron.types import CronSchedule

        name = str(payload.get("name") or "").strip() or "新任务"
        message = str(payload.get("message") or "").strip()
        if not message:
            raise ValueError("message is required")

        schedule_type = str(payload.get("scheduleType") or "every").strip().lower()
        if schedule_type == "every":
            try:
                every_seconds = int(payload.get("everySeconds") or 0)
            except (TypeError, ValueError):
                every_seconds = 0
            if every_seconds <= 0:
                raise ValueError("everySeconds must be > 0")
            schedule = CronSchedule(kind="every", every_ms=every_seconds * 1000)
        elif schedule_type == "cron":
            cron_expr = str(payload.get("cronExpr") or "").strip()
            if not cron_expr:
                raise ValueError("cronExpr is required for cron schedule")
            schedule = CronSchedule(kind="cron", expr=cron_expr)
        elif schedule_type == "at":
            at_time = str(payload.get("atTime") or "").strip()
            if not at_time:
                raise ValueError("atTime is required for at schedule")
            if at_time.endswith("Z"):
                at_time = at_time[:-1] + "+00:00"
            try:
                dt = datetime.datetime.fromisoformat(at_time)
            except ValueError as exc:
                raise ValueError("invalid atTime format, expected ISO datetime") from exc
            schedule = CronSchedule(kind="at", at_ms=int(dt.timestamp() * 1000))
        else:
            raise ValueError("scheduleType must be one of: every, cron, at")

        service = self._cron_service()
        job = service.add_job(name=name, schedule=schedule, message=message)
        return {
            "ok": True,
            "job": self._cron_job_to_dict(job),
        }

    def toggle_cron_job(self, payload: dict[str, Any]) -> dict[str, Any]:
        job_id = str(payload.get("jobId") or "").strip()
        if not job_id:
            raise ValueError("jobId is required")

        enabled = self._as_bool(payload.get("enabled"))
        service = self._cron_service()
        job = service.enable_job(job_id, enabled=enabled)
        if not job:
            raise ValueError(f"job not found: {job_id}")
        return {
            "ok": True,
            "job": self._cron_job_to_dict(job),
        }

    def remove_cron_job(self, payload: dict[str, Any]) -> dict[str, Any]:
        job_id = str(payload.get("jobId") or "").strip()
        if not job_id:
            raise ValueError("jobId is required")

        service = self._cron_service()
        removed = service.remove_job(job_id)
        if not removed:
            raise ValueError(f"job not found: {job_id}")
        return {"ok": True, "jobId": job_id}

    def run_cron_job(self, payload: dict[str, Any]) -> dict[str, Any]:
        job_id = str(payload.get("jobId") or "").strip()
        if not job_id:
            raise ValueError("jobId is required")

        force = self._as_bool(payload.get("force"))
        service = self._cron_service()
        ok = asyncio.run(service.run_job(job_id, force=force))
        if not ok:
            raise ValueError(f"failed to run job: {job_id}")
        return {"ok": True, "jobId": job_id}


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

    def _read_chunked_body(self, max_bytes: int = 2_000_000) -> bytes:
        body = bytearray()

        while True:
            size_line = self.rfile.readline(65537)
            if not size_line:
                raise ValueError("invalid chunked body")

            size_token = size_line.split(b";", 1)[0].strip()
            try:
                chunk_size = int(size_token, 16)
            except ValueError as exc:
                raise ValueError("invalid chunked body") from exc

            if chunk_size < 0:
                raise ValueError("invalid chunked body")

            if chunk_size == 0:
                # Trailer headers end with an empty line.
                while True:
                    trailer_line = self.rfile.readline(65537)
                    if not trailer_line or trailer_line in {b"\r\n", b"\n"}:
                        return bytes(body)
                
            if len(body) + chunk_size > max_bytes:
                raise ValueError("request body too large")

            chunk = self.rfile.read(chunk_size)
            if len(chunk) != chunk_size:
                raise ValueError("invalid chunked body")
            body.extend(chunk)

            chunk_ending = self.rfile.readline(65537)
            if chunk_ending not in {b"\r\n", b"\n"}:
                raise ValueError("invalid chunked body")

    def _read_json(self) -> dict[str, Any]:
        self._last_raw_body = b""

        transfer_encoding = (self.headers.get("Transfer-Encoding") or "").lower()
        raw = b""

        if "chunked" in transfer_encoding:
            raw = self._read_chunked_body()
        else:
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                length = 0

            if length > 0:
                raw = self.rfile.read(length)

        self._last_raw_body = raw or b""
        if not raw:
            return {}

        try:
            data = json.loads(raw.decode("utf-8"))
            return data if isinstance(data, dict) else {}
        except json.JSONDecodeError as exc:
            raise ValueError("invalid JSON body") from exc

    def _request_debug_info(self, include_raw_body: bool = False) -> dict[str, Any]:
        info: dict[str, Any] = {
            "client": self.client_address[0] if self.client_address else "",
            "path": self.path,
            "contentType": self.headers.get("Content-Type", ""),
            "contentLength": self.headers.get("Content-Length", ""),
            "headers": {k: v for k, v in self.headers.items()},
        }
        if include_raw_body:
            raw_body = getattr(self, "_last_raw_body", b"")
            if raw_body:
                raw_preview = raw_body.decode("utf-8", errors="replace")
                if len(raw_preview) > 2000:
                    raw_preview = raw_preview[:2000] + "...(truncated)"
            else:
                raw_preview = ""
            info["rawBodyPreview"] = raw_preview
        return info

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

        if parsed.path == "/api/sessions":
            query = parse_qs(parsed.query)
            channel = (query.get("channel") or ["webui"])[0].strip() or "webui"
            try:
                limit = int((query.get("limit") or ["200"])[0] or 200)
            except ValueError:
                self._send_json(400, {"error": "invalid query parameter"})
                return
            limit = max(1, min(limit, 500))
            try:
                self._send_json(200, self.runtime.list_sessions(channel=channel, limit=limit))
            except Exception as exc:
                self._send_json(500, {"error": str(exc)})
            return

        if parsed.path == "/api/cron/jobs":
            include_disabled = (parse_qs(parsed.query).get("all") or ["1"])[0].strip() != "0"
            try:
                self._send_json(200, self.runtime.list_cron_jobs(include_disabled=include_disabled))
            except Exception as exc:
                self._send_json(500, {"error": str(exc)})
            return

        if parsed.path == "/api/webhook/events":
            query = parse_qs(parsed.query)
            try:
                since_id = int((query.get("since") or ["0"])[0] or 0)
                limit = int((query.get("limit") or ["80"])[0] or 80)
            except ValueError:
                self._send_json(400, {"error": "invalid query parameter"})
                return
            self._send_json(200, self.runtime.list_webhook_events(since_id=since_id, limit=limit))
            return

        if parsed.path == "/api/webhook/request":
            # Wisdom Caixin callback availability check expects this exact payload.
            self.runtime.record_webhook_event(
                "validation_get",
                "收到智慧财信可用性校验 GET 请求",
                detail={"client": self.client_address[0] if self.client_address else ""},
            )
            self._send_json(200, {"result": "ok"})
            return

        if parsed.path == "/api/memory/list":
            from pathlib import Path
            workspace_dir = Path(__file__).resolve().parent.parent.parent / "workspace"
            mem_files = []
            if workspace_dir.exists():
                mem_files.extend(list(workspace_dir.glob("*.md")))
            mem_dir = workspace_dir / "memory"
            if mem_dir.exists():
                mem_files.extend(list(mem_dir.glob("*.md")))
            
            items = []
            import datetime
            for f in sorted(list(set(mem_files)), key=lambda x: x.stat().st_mtime, reverse=True):
                mtime = datetime.datetime.fromtimestamp(f.stat().st_mtime).isoformat()
                items.append({
                    "name": f.name if f.parent == workspace_dir else f"memory/{f.name}",
                    "path": str(f.resolve()),
                    "size": f.stat().st_size,
                    "updated_at": mtime
                })
            self._send_json(200, {"files": items})
            return

        if parsed.path == "/api/skills/list":
            from pathlib import Path
            query = parse_qs(parsed.query)
            skills_dir = Path(__file__).resolve().parent.parent / "skills"
            req_dir = query.get("dir", [None])[0]
            
            items = []
            import datetime
            if req_dir:
                target_dir = skills_dir / req_dir
                if target_dir.exists() and target_dir.is_dir():
                    for f in sorted(target_dir.iterdir(), key=lambda x: x.name):
                        if f.is_file():
                            mtime = datetime.datetime.fromtimestamp(f.stat().st_mtime).isoformat()
                            items.append({
                                "name": f.name,
                                "path": str(f.resolve()),
                                "is_dir": False,
                                "updated_at": mtime
                            })
            else:
                if skills_dir.exists():
                    for f in sorted(skills_dir.iterdir(), key=lambda x: x.name):
                        if f.name == ".DS_Store": continue
                        mtime = datetime.datetime.fromtimestamp(f.stat().st_mtime).isoformat()
                        items.append({
                            "name": f.name,
                            "path": str(f.resolve()),
                            "is_dir": f.is_dir(),
                            "updated_at": mtime
                        })
            self._send_json(200, {"skills": items})
            return

        self._send_json(404, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        try:
            payload = self._read_json()
        except ValueError as exc:
            if parsed.path == "/api/webhook/request":
                self.runtime.record_webhook_event(
                    "invalid_json",
                    f"入站回调 JSON 解析失败: {exc}",
                    detail={
                        "path": parsed.path,
                        "http": self._request_debug_info(include_raw_body=True),
                    },
                    level="warning",
                )
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

            if parsed.path == "/api/chat/stream":
                message = str(payload.get("message") or "").strip()
                if not message:
                    self._send_json(400, {"error": "message is required"})
                    return
                session_id = str(payload.get("sessionId") or secrets.token_hex(8)).strip()
                display_message = str(payload.get("displayMessage") or "").strip() or None
                raw_attachments = payload.get("attachments")
                attachments: list[dict[str, Any]] = []
                if isinstance(raw_attachments, list):
                    for item in raw_attachments[:10]:
                        if isinstance(item, dict) and item.get("name"):
                            attachments.append({k: item[k] for k in ("name", "size", "type") if k in item})

                config = load_config()
                provider = self.runtime._make_provider(config)
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
                metadata: dict[str, Any] = {}
                if display_message:
                    metadata["displayContent"] = display_message
                if attachments:
                    metadata["attachments"] = attachments

                # Send SSE headers
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream; charset=utf-8")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Connection", "keep-alive")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()

                def send_sse(data: dict) -> bool:
                    try:
                        line = "data: " + json.dumps(data, ensure_ascii=False) + "\n\n"
                        self.wfile.write(line.encode("utf-8"))
                        self.wfile.flush()
                        return True
                    except (BrokenPipeError, ConnectionResetError):
                        return False

                async def run_stream():
                    async for event in agent.process_direct_streaming(
                        content=message,
                        session_key=f"webui:{session_id}",
                        channel="webui",
                        chat_id=session_id,
                        metadata=metadata,
                    ):
                        if not send_sse(event):
                            break
                        if event.get("type") == "done":
                            break

                try:
                    asyncio.run(run_stream())
                except Exception as exc:
                    send_sse({"type": "error", "message": str(exc)})
                return

            if parsed.path == "/api/sessions/remove":
                session_id = str(payload.get("sessionId", "")).strip()
                if not session_id:
                    self._send_json(400, {"error": "sessionId is required"})
                    return
                result = self.runtime.remove_session(session_id, channel="webui")
                self._send_json(200, result)
                return

            if parsed.path == "/api/files/read":
                from pathlib import Path
                filepath = Path(payload.get("path", ""))
                if filepath.exists() and filepath.is_file():
                    content = filepath.read_text(encoding="utf-8")
                    self._send_json(200, {"content": content})
                else:
                    self._send_json(404, {"error": "file not found"})
                return

            if parsed.path == "/api/files/save":
                from pathlib import Path
                filepath = Path(payload.get("path", ""))
                content = payload.get("content", "")
                if filepath.exists() and filepath.is_file():
                    filepath.write_text(content, encoding="utf-8")
                    self._send_json(200, {"result": "ok"})
                else:
                    self._send_json(404, {"error": "file not found"})
                return

            if parsed.path == "/api/cron/jobs":
                result = self.runtime.add_cron_job(payload)
                self._send_json(200, result)
                return

            if parsed.path == "/api/cron/toggle":
                result = self.runtime.toggle_cron_job(payload)
                self._send_json(200, result)
                return

            if parsed.path == "/api/cron/remove":
                result = self.runtime.remove_cron_job(payload)
                self._send_json(200, result)
                return

            if parsed.path == "/api/cron/run":
                result = self.runtime.run_cron_job(payload)
                self._send_json(200, result)
                return

            if parsed.path == "/api/webhook/zhcx-test-send":
                result = self.runtime.send_zhcx_test_message(payload)
                self._send_json(200, result)
                return

            if parsed.path == "/api/webhook/request":
                host = self.headers.get("Host") or f"{self.runtime._public_host()}:{self.runtime.port}"
                proto = self.headers.get("X-Forwarded-Proto") or "http"
                self.runtime.record_webhook_event(
                    "inbound_http",
                    f"收到Webhook HTTP POST content-type={self.headers.get('Content-Type') or '-'} content-length={self.headers.get('Content-Length') or '0'} transfer-encoding={self.headers.get('Transfer-Encoding') or '-'}",
                    detail={
                        "http": self._request_debug_info(include_raw_body=True),
                        "payloadKeys": sorted(str(k) for k in payload.keys()),
                    },
                )
                payload["_currentRequestUrl"] = f"{proto}://{host}{parsed.path}"
                result = self.runtime.handle_webhook(payload)
                self._send_json(200, result)
                return

            self._send_json(404, {"error": "not found"})
        except ValueError as exc:
            if parsed.path == "/api/webhook/request":
                self.runtime.record_webhook_event(
                    "handler_error",
                    f"处理入站回调失败: {exc}",
                    detail={
                        "path": parsed.path,
                        "payload": payload,
                        "http": self._request_debug_info(include_raw_body=True),
                    },
                    level="warning",
                )
            self._send_json(
                400,
                {
                    "error": str(exc),
                    "detail": {
                        "type": type(exc).__name__,
                        "path": parsed.path,
                    },
                },
            )
        except Exception as exc:
            if parsed.path == "/api/webhook/request":
                self.runtime.record_webhook_event(
                    "handler_error",
                    f"处理入站回调异常: {exc}",
                    detail={
                        "path": parsed.path,
                        "payload": payload,
                        "http": self._request_debug_info(include_raw_body=True),
                    },
                    level="error",
                )
            detail: dict[str, Any] = {
                "type": type(exc).__name__,
                "path": parsed.path,
            }
            if parsed.path == "/api/chat":
                detail["traceback"] = traceback.format_exc(limit=8)
            self._send_json(500, {"error": str(exc), "detail": detail})


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
