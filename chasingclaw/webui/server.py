"""Minimal Web UI + webhook server for chasingclaw."""

from __future__ import annotations

import asyncio
import datetime
import hashlib
import json
import os
import secrets
import socket
import threading
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


UI_HTML = """<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>chasingclaw UI</title>
  <style>
    body { font-family: -apple-system, BlinkMacSystemFont, Segoe UI, Roboto, sans-serif; margin: 0; background: #0f172a; color: #e2e8f0; }
    .wrap { max-width: 1280px; margin: 0 auto; padding: 16px; display: grid; grid-template-columns: 420px 1fr; gap: 16px; }
    .card { background: #111827; border: 1px solid #1f2937; border-radius: 10px; padding: 14px; }
    h1 { margin: 0 0 8px; font-size: 20px; }
    h2 { margin: 0 0 10px; font-size: 15px; }
    h3 { margin: 12px 0 8px; font-size: 13px; color: #93c5fd; }
    label { display: block; margin: 10px 0 5px; font-size: 12px; color: #94a3b8; }
    input, select, textarea, button {
      width: 100%; box-sizing: border-box; border-radius: 8px; border: 1px solid #334155;
      background: #0b1220; color: #e2e8f0; padding: 10px; font-size: 13px;
    }
    textarea { min-height: 72px; resize: vertical; }
    button { background: #2563eb; border: 0; cursor: pointer; font-weight: 600; margin-top: 8px; }
    button:hover { background: #1d4ed8; }
    button.secondary { background: #475569; }
    button.teal { background: #0f766e; }
    .row { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }
    .chat-toolbar { display: flex; align-items: center; justify-content: space-between; gap: 8px; margin-bottom: 8px; }
    .chat-log {
      height: 60vh; overflow-y: auto; background: #020617; border-radius: 8px;
      padding: 12px; border: 1px solid #1e293b; display: flex; flex-direction: column; gap: 8px;
    }
    .chat-row { display: flex; }
    .chat-row.user { justify-content: flex-end; }
    .chat-row.assistant { justify-content: flex-start; }
    .chat-row.system { justify-content: center; }
    .msg {
      max-width: 82%; padding: 8px 10px; border-radius: 10px; white-space: pre-wrap;
      word-break: break-word; border: 1px solid transparent;
    }
    .msg.user { background: #1e3a8a; border-color: #1d4ed8; }
    .msg.assistant { background: #064e3b; border-color: #047857; }
    .msg.system { max-width: 96%; background: #1f2937; border-color: #334155; color: #cbd5e1; font-size: 12px; }
    .msg.pending { opacity: 0.75; font-style: italic; }
    .msg-meta { margin-top: 6px; font-size: 11px; color: #cbd5e1; opacity: 0.7; text-align: right; }
    .msg-meta.system { text-align: left; }
    .inline-btn { width: auto; margin-top: 0; padding: 6px 10px; }
    .status { font-size: 12px; color: #94a3b8; margin-top: 8px; }
    .hint { font-size: 11px; color: #94a3b8; margin-top: 6px; }
    .check-row { display: flex; align-items: center; gap: 8px; margin-top: 10px; }
    .check-row input { width: auto; }
    .cron-list { margin-top: 10px; border: 1px solid #1e293b; border-radius: 8px; overflow: hidden; }
    .cron-head, .cron-item { display: grid; grid-template-columns: 0.9fr 1fr 1fr 1.8fr; gap: 8px; padding: 8px 10px; align-items: center; }
    .cron-head { background: #0b1220; font-size: 12px; color: #94a3b8; }
    .cron-item { border-top: 1px solid #1e293b; font-size: 12px; }
    .cron-actions { display: flex; gap: 6px; }
    .cron-actions button { margin-top: 0; padding: 6px 8px; font-size: 12px; }
    @media (max-width: 980px) { .wrap { grid-template-columns: 1fr; } }
  </style>
</head>
<body>
  <div class="wrap">
    <div class="card">
      <h1>chasingclaw</h1>
      <h2>模型 / Webhook / 运行配置</h2>

      <h3>模型配置</h3>
      <label>Provider</label>
      <select id="provider"></select>

      <label>Model</label>
      <input id="model" placeholder="如 anthropic/claude-opus-4-5" />

      <label>API Base URL</label>
      <input id="apiBase" placeholder="可选，例如 http://localhost:8000/v1" />

      <label>API Key</label>
      <input id="apiKey" type="password" placeholder="输入后点击保存" />

      <h3>智慧财信Webhook配置</h3>
      <label>智慧财信机器人webhook地址</label>
      <input id="webhookCallbackUrl" placeholder="例如 https://your-im-server/webhook" />

      <label>用于配置智慧财信机器人的webhook回调地址</label>
      <input id="inboundWebhookUrl" readonly />

      <button id="advancedToggleBtn" type="button" class="secondary">高级配置</button>

      <div id="advancedSection" style="display:none;">
        <h3>运行配置</h3>
        <div class="check-row">
          <input id="restrictToWorkspace" type="checkbox" />
          <label for="restrictToWorkspace" style="margin:0;">Restrict tools to workspace</label>
        </div>

        <div class="row">
          <div>
            <label>Webhook Timeout (秒)</label>
            <input id="webhookTimeoutSeconds" type="number" min="1" step="1" />
          </div>
          <div>
            <label>回复消息类型</label>
            <select id="webhookMessageType">
              <option value="text">text</option>
              <option value="markdown">markdown</option>
              <option value="link">link</option>
            </select>
          </div>
        </div>

        <label>消息模板（支持 {reply} 占位符）</label>
        <textarea id="webhookMessageTemplate" placeholder="默认: {reply}"></textarea>

        <div class="row">
          <div>
            <label>link.title</label>
            <input id="webhookLinkTitle" placeholder="chasingclaw 回复" />
          </div>
          <div>
            <label>link.btnTitle</label>
            <input id="webhookLinkButtonTitle" placeholder="查看详情" />
          </div>
        </div>

        <label>link.messageUrl（可选）</label>
        <input id="webhookLinkMessageUrl" placeholder="例如 https://your-site/details" />

        <h3>签名配置（可选）</h3>
        <div class="row">
          <div>
            <label>签名 key</label>
            <input id="webhookSignKey" placeholder="Authorization 的 key" />
          </div>
          <div>
            <label>签名 secret</label>
            <input id="webhookSignSecret" type="password" placeholder="Authorization 签名 secret" />
          </div>
        </div>
        <div class="hint">配置后将自动携带 Content-Md5 / Content-Type / DATE / Authorization 请求头。</div>
      </div>

      <button id="saveBtn">保存配置</button>
      <div class="status" id="cfgStatus"></div>
    </div>

    <div class="card">
      <h2>Chat</h2>
      <div class="chat-toolbar">
        <div class="status" id="sessionInfo" style="margin-top:0;"></div>
        <button id="clearChatBtn" type="button" class="secondary inline-btn">清空窗口</button>
      </div>
      <div class="chat-log" id="chatLog"></div>
      <div class="row" style="margin-top:10px;">
        <input id="message" placeholder="输入消息，Enter发送" />
        <button id="sendBtn">发送</button>
      </div>
      <div class="row" style="margin-top:8px;">
        <button id="newSessionBtn" class="secondary">新会话</button>
        <button id="webhookTestBtn" class="teal">测试智慧财信发送</button>
      </div>
      <div class="status" id="chatStatus"></div>
    </div>

    <div class="card" id="cronCard" style="display:none;">
      <h2>Cron 定时任务</h2>
      <div class="row">
        <div>
          <label>任务名称</label>
          <input id="cronName" placeholder="例如 每日巡检" />
        </div>
        <div>
          <label>计划类型</label>
          <select id="cronScheduleType">
            <option value="every">every（每隔秒）</option>
            <option value="cron">cron 表达式</option>
            <option value="at">at（一次性）</option>
          </select>
        </div>
      </div>

      <label>任务消息（交给 AI 执行）</label>
      <textarea id="cronMessage" placeholder="例如 请总结今天项目进展"></textarea>

      <div class="row">
        <div>
          <label>every 秒数</label>
          <input id="cronEverySeconds" type="number" min="1" step="1" value="3600" />
        </div>
        <div>
          <label>cron 表达式</label>
          <input id="cronExpr" placeholder="例如 0 9 * * *" />
        </div>
      </div>

      <label>at 时间（本地时间）</label>
      <input id="cronAt" type="datetime-local" />

      <div class="row" style="margin-top:8px;">
        <button id="cronAddBtn">新增任务</button>
        <button id="cronRefreshBtn" class="secondary">刷新列表</button>
      </div>

      <div class="status" id="cronStatus"></div>
      <div class="cron-list" id="cronList"></div>
    </div>
  </div>

<script>
const KEY = 'chasingclaw_webui_session_id';

function createSessionId() {
  if (window.crypto && typeof window.crypto.randomUUID === 'function') {
    return window.crypto.randomUUID();
  }
  return 'sid-' + Date.now().toString(36) + '-' + Math.random().toString(36).slice(2, 10);
}

let sessionId = localStorage.getItem(KEY) || createSessionId();
localStorage.setItem(KEY, sessionId);

const el = (id) => document.getElementById(id);
const chatLog = el('chatLog');
const DEFAULT_PROVIDER_OPTIONS = ['openrouter', 'openai', 'anthropic', 'deepseek', 'custom'];
let webhookEventCursor = 0;
let webhookPollTimer = null;

function formatMessageTime(value) {
  if (!value) return new Date().toLocaleTimeString();
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return new Date().toLocaleTimeString();
  return d.toLocaleTimeString();
}

function appendMessage(role, text, options = {}) {
  const roleClass = role === 'user' ? 'user' : role === 'assistant' ? 'assistant' : 'system';

  const row = document.createElement('div');
  row.className = 'chat-row ' + roleClass;

  const bubble = document.createElement('div');
  bubble.className = 'msg ' + roleClass;
  if (options.pending) {
    bubble.classList.add('pending');
  }

  const content = document.createElement('div');
  content.textContent = text || '';
  bubble.appendChild(content);

  const meta = document.createElement('div');
  meta.className = 'msg-meta ' + roleClass;
  meta.textContent = formatMessageTime(options.timestamp);
  bubble.appendChild(meta);

  row.appendChild(bubble);
  chatLog.appendChild(row);
  chatLog.scrollTop = chatLog.scrollHeight;
  return row;
}

function escapeHtml(text) {
  return String(text || '')
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#39;');
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

function updateWebhookMessageTypeState() {
  const isLink = el('webhookMessageType').value === 'link';
  el('webhookLinkTitle').disabled = !isLink;
  el('webhookLinkMessageUrl').disabled = !isLink;
  el('webhookLinkButtonTitle').disabled = !isLink;
}

function updateCronScheduleInputs() {
  const scheduleType = el('cronScheduleType').value;
  el('cronEverySeconds').disabled = scheduleType !== 'every';
  el('cronExpr').disabled = scheduleType !== 'cron';
  el('cronAt').disabled = scheduleType !== 'at';
}

function setAdvancedVisible(visible) {
  el('advancedSection').style.display = visible ? 'block' : 'none';
  el('cronCard').style.display = visible ? 'block' : 'none';
  el('advancedToggleBtn').textContent = visible ? '收起高级配置' : '高级配置';
}

function toggleAdvanced() {
  const showing = el('advancedSection').style.display !== 'none';
  setAdvancedVisible(!showing);
}

function setProviderOptions(options, selected) {
  const provider = el('provider');
  provider.innerHTML = '';

  for (const p of options) {
    const option = document.createElement('option');
    option.value = p;
    option.textContent = p;
    provider.appendChild(option);
  }

  const providerValue = selected || options[0] || 'openrouter';
  if (![...provider.options].some((o) => o.value === providerValue) && providerValue) {
    const option = document.createElement('option');
    option.value = providerValue;
    option.textContent = providerValue;
    provider.appendChild(option);
  }

  provider.value = providerValue;
  updateApiBaseState();
}

async function loadConfig() {
  const data = await api('/api/config');
  const options = (data.providerOptions && data.providerOptions.length)
    ? data.providerOptions
    : DEFAULT_PROVIDER_OPTIONS;
  setProviderOptions(options, data.provider || options[0] || 'openrouter');

  el('model').value = data.model || '';
  if (isCustomProvider()) {
    el('apiBase').value = data.apiBase || '';
  }
  el('apiKey').value = data.apiKey || '';
  el('restrictToWorkspace').checked = !!data.restrictToWorkspace;

  el('webhookCallbackUrl').value = data.webhookCallbackUrl || '';
  el('inboundWebhookUrl').value = data.inboundWebhookUrl || (window.location.origin + '/api/webhook/request');
  el('webhookTimeoutSeconds').value = data.webhookTimeoutSeconds || 15;
  el('webhookSignKey').value = data.webhookSignKey || '';
  el('webhookSignSecret').value = data.webhookSignSecret || '';

  el('webhookMessageType').value = data.webhookMessageType || 'text';
  el('webhookMessageTemplate').value = data.webhookMessageTemplate || '{reply}';
  el('webhookLinkTitle').value = data.webhookLinkTitle || 'chasingclaw 回复';
  el('webhookLinkMessageUrl').value = data.webhookLinkMessageUrl || '';
  el('webhookLinkButtonTitle').value = data.webhookLinkButtonTitle || '查看详情';
  updateWebhookMessageTypeState();

  el('cfgStatus').textContent = '配置已加载';
}

function formatWebhookEvent(event) {
  if (!event) {
    return '[Webhook] 收到空事件';
  }

  let text = '[Webhook] ' + (event.summary || event.event || '收到事件');
  if (event.detail) {
    try {
      text += '\\n' + JSON.stringify(event.detail, null, 2);
    } catch (err) {
      text += '\\n(detail parse failed)';
    }
  }
  return text;
}

async function loadWebhookEvents(options = {}) {
  const reset = !!options.reset;
  const since = reset ? 0 : webhookEventCursor;
  const limit = reset ? 30 : 80;
  const data = await api('/api/webhook/events?since=' + encodeURIComponent(String(since)) + '&limit=' + encodeURIComponent(String(limit)));

  for (const event of data.events || []) {
    appendMessage('system', formatWebhookEvent(event), { timestamp: event.timestamp });
    const eventId = Number(event.id || 0);
    if (eventId > webhookEventCursor) {
      webhookEventCursor = eventId;
    }
  }

  const lastId = Number(data.lastId || 0);
  if (lastId > webhookEventCursor) {
    webhookEventCursor = lastId;
  }
}

function startWebhookEventPolling() {
  if (webhookPollTimer) {
    clearInterval(webhookPollTimer);
  }

  webhookPollTimer = window.setInterval(async () => {
    try {
      await loadWebhookEvents();
    } catch (err) {
      // Keep silent for temporary polling errors.
    }
  }, 2500);
}

async function loadHistory() {
  chatLog.innerHTML = '';
  const data = await api('/api/history?sessionId=' + encodeURIComponent(sessionId));
  for (const item of data.messages || []) {
    if (item.role === 'user' || item.role === 'assistant') {
      appendMessage(item.role === 'user' ? 'user' : 'assistant', item.content || '', { timestamp: item.timestamp });
    }
  }

  webhookEventCursor = 0;
  await loadWebhookEvents({ reset: true });
}

async function saveConfig() {
  el('cfgStatus').textContent = '保存中...';
  const payload = {
    provider: el('provider').value,
    model: el('model').value,
    apiKey: el('apiKey').value,
    apiBase: isCustomProvider() ? el('apiBase').value : '',
    restrictToWorkspace: el('restrictToWorkspace').checked,
    webhookCallbackUrl: el('webhookCallbackUrl').value,
    webhookTimeoutSeconds: Number(el('webhookTimeoutSeconds').value || 15),
    webhookSignKey: el('webhookSignKey').value,
    webhookSignSecret: el('webhookSignSecret').value,
    webhookMessageType: el('webhookMessageType').value,
    webhookMessageTemplate: el('webhookMessageTemplate').value,
    webhookLinkTitle: el('webhookLinkTitle').value,
    webhookLinkMessageUrl: el('webhookLinkMessageUrl').value,
    webhookLinkButtonTitle: el('webhookLinkButtonTitle').value,
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

  const pendingRow = appendMessage('assistant', '正在思考中...', { pending: true });
  el('chatStatus').textContent = '思考中...';

  try {
    const data = await api('/api/chat', {
      method: 'POST',
      body: JSON.stringify({ message, sessionId: sessionId })
    });
    pendingRow.remove();
    appendMessage('assistant', data.reply || '');
    el('chatStatus').textContent = '';
  } catch (err) {
    pendingRow.remove();
    appendMessage('assistant', 'Error: ' + err.message);
    el('chatStatus').textContent = '发送失败';
  }
}

async function testWebhook() {
  const input = el('message');
  const message = (input.value || 'chasingclaw 智慧财信联通测试').trim();
  const pendingRow = appendMessage('assistant', '正在发送智慧财信测试消息...', { pending: true });
  el('chatStatus').textContent = '正在向智慧财信机器人发送测试消息...';
  try {
    const data = await api('/api/webhook/zhcx-test-send', {
      method: 'POST',
      body: JSON.stringify({ message }),
    });
    pendingRow.remove();
    appendMessage('assistant', '[智慧财信测试] 已发送：' + message);
    if (data.result) {
      appendMessage('assistant', '[智慧财信测试返回] ' + JSON.stringify(data.result));
    }
    el('chatStatus').textContent = '智慧财信测试发送完成';
  } catch (err) {
    pendingRow.remove();
    appendMessage('assistant', '智慧财信测试失败: ' + err.message);
    el('chatStatus').textContent = '智慧财信测试失败';
  }
}

function formatCronTime(ts) {
  if (!ts) return '-';
  const d = new Date(ts);
  return d.toLocaleString();
}

function scheduleText(job) {
  if (job.scheduleKind === 'every') return 'every ' + (job.everySeconds || 0) + 's';
  if (job.scheduleKind === 'cron') return job.cronExpr || '';
  if (job.scheduleKind === 'at') return job.atIso || '';
  return '';
}

function clearChatWindow() {
  chatLog.innerHTML = '';
  el('chatStatus').textContent = '';
}

async function loadCronJobs() {
  const data = await api('/api/cron/jobs?all=1');
  const jobs = data.jobs || [];
  const container = el('cronList');

  let html = '<div class="cron-head"><div>ID</div><div>任务</div><div>计划</div><div>操作</div></div>';
  if (!jobs.length) {
    html += '<div class="cron-item"><div>-</div><div>暂无任务</div><div>-</div><div>-</div></div>';
  } else {
    for (const j of jobs) {
      const status = j.enabled ? '启用' : '禁用';
      const meta = status + ' / next: ' + formatCronTime(j.nextRunAtMs);
      html += '<div class="cron-item">' +
        '<div>' + escapeHtml(j.id) + '</div>' +
        '<div>' + escapeHtml(j.name) + '<div class="hint">' + escapeHtml(meta) + '</div></div>' +
        '<div>' + escapeHtml(scheduleText(j)) + '</div>' +
        '<div class="cron-actions">' +
          '<button class="secondary" data-act="toggle" data-id="' + escapeHtml(j.id) + '" data-enabled="' + (j.enabled ? '1' : '0') + '">' + (j.enabled ? '禁用' : '启用') + '</button>' +
          '<button class="teal" data-act="run" data-id="' + escapeHtml(j.id) + '">执行</button>' +
          '<button data-act="remove" data-id="' + escapeHtml(j.id) + '">删除</button>' +
        '</div>' +
      '</div>';
    }
  }

  container.innerHTML = html;
}

async function addCronJob() {
  el('cronStatus').textContent = '保存任务中...';
  const payload = {
    name: el('cronName').value.trim() || '新任务',
    message: el('cronMessage').value.trim(),
    scheduleType: el('cronScheduleType').value,
    everySeconds: Number(el('cronEverySeconds').value || 0),
    cronExpr: el('cronExpr').value.trim(),
    atTime: el('cronAt').value,
  };
  if (!payload.message) {
    el('cronStatus').textContent = '任务消息不能为空';
    return;
  }
  try {
    await api('/api/cron/jobs', {
      method: 'POST',
      body: JSON.stringify(payload),
    });
    el('cronStatus').textContent = '任务已创建';
    await loadCronJobs();
  } catch (err) {
    el('cronStatus').textContent = '创建失败: ' + err.message;
  }
}

async function onCronAction(e) {
  const btn = e.target.closest('button[data-act]');
  if (!btn) return;
  const action = btn.getAttribute('data-act');
  const jobId = btn.getAttribute('data-id');
  try {
    if (action === 'toggle') {
      const enabled = btn.getAttribute('data-enabled') !== '1';
      await api('/api/cron/toggle', { method: 'POST', body: JSON.stringify({ jobId, enabled }) });
      el('cronStatus').textContent = enabled ? '任务已启用' : '任务已禁用';
    } else if (action === 'remove') {
      await api('/api/cron/remove', { method: 'POST', body: JSON.stringify({ jobId }) });
      el('cronStatus').textContent = '任务已删除';
    } else if (action === 'run') {
      await api('/api/cron/run', { method: 'POST', body: JSON.stringify({ jobId, force: true }) });
      el('cronStatus').textContent = '任务已执行';
    }
    await loadCronJobs();
  } catch (err) {
    el('cronStatus').textContent = '操作失败: ' + err.message;
  }
}

el('saveBtn').addEventListener('click', saveConfig);
el('provider').addEventListener('change', updateApiBaseState);
el('advancedToggleBtn').addEventListener('click', toggleAdvanced);
el('webhookMessageType').addEventListener('change', updateWebhookMessageTypeState);
el('sendBtn').addEventListener('click', sendChat);
el('webhookTestBtn').addEventListener('click', testWebhook);
el('clearChatBtn').addEventListener('click', clearChatWindow);
el('newSessionBtn').addEventListener('click', async () => {
  sessionId = createSessionId();
  localStorage.setItem(KEY, sessionId);
  setSessionInfo();
  await loadHistory();
});
el('message').addEventListener('keydown', (e) => {
  if (e.key === 'Enter') sendChat();
});

el('cronScheduleType').addEventListener('change', updateCronScheduleInputs);
el('cronAddBtn').addEventListener('click', addCronJob);
el('cronRefreshBtn').addEventListener('click', loadCronJobs);
el('cronList').addEventListener('click', onCronAction);

(async () => {
  try {
    setAdvancedVisible(false);
    setSessionInfo();
    await loadConfig();
    await loadHistory();
    updateCronScheduleInputs();
    await loadCronJobs();
    startWebhookEventPolling();
  } catch (err) {
    el('cfgStatus').textContent = '配置加载失败：' + (err.message || String(err));
    // Fallback: keep provider selector/inbound callback usable even if /api/config fails.
    setProviderOptions(DEFAULT_PROVIDER_OPTIONS, 'openrouter');
    el('inboundWebhookUrl').value = window.location.origin + '/api/webhook/request';
    try {
      await loadWebhookEvents({ reset: true });
    } catch (_) {
      // Ignore fallback polling bootstrap failures.
    }
    startWebhookEventPolling();
  }
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

    def _read_json(self) -> dict[str, Any]:
        self._last_raw_body = b""
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        if length <= 0:
            return {}

        raw = self.rfile.read(length)
        self._last_raw_body = raw or b""
        if not raw:
            return {}

        try:
            data = json.loads(raw.decode("utf-8"))
            return data if isinstance(data, dict) else {}
        except json.JSONDecodeError:
            raise ValueError("invalid JSON body")

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
                    f"收到Webhook HTTP POST content-type={self.headers.get('Content-Type') or '-'} content-length={self.headers.get('Content-Length') or '0'}",
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
            self._send_json(400, {"error": str(exc)})
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
