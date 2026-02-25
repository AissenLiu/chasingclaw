"""LiteLLM provider implementation for multi-provider support."""

import json
import os
from typing import Any

import litellm
from litellm import acompletion

from chasingclaw.providers.base import LLMProvider, LLMResponse, ToolCallRequest
from chasingclaw.providers.registry import PROVIDERS, find_by_model, find_by_name, find_gateway


OPENAI_COMPAT_PREFIXES = {
    spec.name for spec in PROVIDERS if spec.name
}.union(
    spec.litellm_prefix for spec in PROVIDERS if spec.litellm_prefix
).union(
    {
        # Common aliases seen in model strings.
        "zhipu",
        "hosted_vllm",
    }
)


class LiteLLMProvider(LLMProvider):
    """
    LLM provider using LiteLLM for multi-provider support.
    
    Supports OpenRouter, Anthropic, OpenAI, Gemini, MiniMax, and many other providers through
    a unified interface.  Provider-specific logic is driven by the registry
    (see providers/registry.py) — no if-elif chains needed here.
    """
    
    def __init__(
        self, 
        api_key: str | None = None, 
        api_base: str | None = None,
        default_model: str = "anthropic/claude-opus-4-5",
        extra_headers: dict[str, str] | None = None,
        provider_name: str | None = None,
    ):
        super().__init__(api_key, api_base)
        self.default_model = default_model
        self.extra_headers = extra_headers or {}
        self.provider_name = (provider_name or "").strip().lower()
        
        # Detect gateway / local deployment.
        # provider_name (from config key) is the primary signal;
        # api_key / api_base are fallback for auto-detection.
        self._gateway = find_gateway(provider_name, api_key, api_base)
        
        # Configure environment variables
        if api_key:
            self._setup_env(api_key, api_base, default_model)
        
        if api_base:
            litellm.api_base = api_base
        
        # Disable LiteLLM logging noise
        litellm.suppress_debug_info = True
        # Drop unsupported parameters for providers (e.g., gpt-5 rejects some params)
        litellm.drop_params = True
    
    def _setup_env(self, api_key: str, api_base: str | None, model: str) -> None:
        """Set environment variables based on detected provider."""
        spec = self._gateway
        if not spec and self.provider_name:
            spec = find_by_name(self.provider_name)
        if not spec:
            spec = find_by_model(model)
        if not spec:
            return

        # Gateway/local overrides existing env; standard provider doesn't
        if self._gateway:
            os.environ[spec.env_key] = api_key
        else:
            os.environ.setdefault(spec.env_key, api_key)

        # Resolve env_extras placeholders:
        #   {api_key}  → user's API key
        #   {api_base} → user's api_base, falling back to spec.default_api_base
        effective_base = api_base or spec.default_api_base
        for env_name, env_val in spec.env_extras:
            resolved = env_val.replace("{api_key}", api_key)
            resolved = resolved.replace("{api_base}", effective_base)
            os.environ.setdefault(env_name, resolved)
    
    def _resolve_model(self, model: str) -> str:
        """Resolve model name by applying provider/gateway prefixes."""
        # In OpenAI-compatible mode, always route through openai/.
        # If model has a known provider prefix (e.g. anthropic/claude-*), strip it first.
        if self.provider_name == "openai" and self.api_base:
            if model.startswith("openai/"):
                return model

            normalized = model
            if "/" in model:
                prefix, rest = model.split("/", 1)
                if prefix in OPENAI_COMPAT_PREFIXES and rest:
                    normalized = rest

            return f"openai/{normalized}"

        if self._gateway:
            # Gateway mode: apply gateway prefix, skip provider-specific prefixes
            prefix = self._gateway.litellm_prefix
            if self._gateway.strip_model_prefix:
                model = model.split("/")[-1]
            if prefix and not model.startswith(f"{prefix}/"):
                model = f"{prefix}/{model}"
            return model
        
        # Standard mode: auto-prefix for known providers
        spec = find_by_model(model)
        if spec and spec.litellm_prefix:
            if not any(model.startswith(s) for s in spec.skip_prefixes):
                model = f"{spec.litellm_prefix}/{model}"
        
        return model
    
    def _apply_model_overrides(self, model: str, kwargs: dict[str, Any]) -> None:
        """Apply model-specific parameter overrides from the registry."""
        model_lower = model.lower()
        spec = find_by_model(model)
        if spec:
            for pattern, overrides in spec.model_overrides:
                if pattern in model_lower:
                    kwargs.update(overrides)
                    return
    
    def _sanitize_text(self, value: Any, limit: int = 1200) -> str:
        text = str(value).strip()
        if self.api_key:
            text = text.replace(self.api_key, "***")
        if len(text) > limit:
            return text[:limit] + "...(truncated)"
        return text

    def _format_error(self, err: Exception, context: dict[str, Any] | None = None) -> str:
        """Format provider errors with detailed diagnostics for UI debugging."""
        lines: list[str] = ["Error calling LLM:"]
        lines.append(f"- type: {type(err).__name__}")
        lines.append(f"- message: {self._sanitize_text(err)}")

        status = self._status_code(err)
        if status is not None:
            lines.append(f"- status: {status}")

        response = getattr(err, "response", None)
        if response is not None:
            try:
                body = response.text
            except Exception:
                body = ""
            if body:
                lines.append(f"- response_body: {self._sanitize_text(body)}")

        for attr in ("body", "error", "message", "status_code"):
            if not hasattr(err, attr):
                continue
            value = getattr(err, attr)
            if value in (None, ""):
                continue
            if attr == "status_code" and status is not None:
                continue
            lines.append(f"- {attr}: {self._sanitize_text(value)}")

        if context:
            context_lines: list[str] = []
            for key, value in context.items():
                if value in (None, "", []):
                    continue
                context_lines.append(f"{key}={self._sanitize_text(value, limit=300)}")
            if context_lines:
                lines.append(f"- context: {'; '.join(context_lines)}")

        return "\n".join(lines)

    def _status_code(self, err: Exception) -> int | None:
        response = getattr(err, "response", None)
        if response is None:
            return None
        status = getattr(response, "status_code", None)
        try:
            return int(status) if status is not None else None
        except (TypeError, ValueError):
            return None

    def _can_retry_without_tools(self, err: Exception, tools_used: bool) -> bool:
        """Retry once without tools for OpenAI-compatible intranet endpoints."""
        if not tools_used:
            return False
        if not (self.provider_name == "openai" and self.api_base):
            return False
        return self._status_code(err) == 400

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
    ) -> LLMResponse:
        """
        Send a chat completion request via LiteLLM.
        
        Args:
            messages: List of message dicts with 'role' and 'content'.
            tools: Optional list of tool definitions in OpenAI format.
            model: Model identifier (e.g., 'anthropic/claude-sonnet-4-5').
            max_tokens: Maximum tokens in response.
            temperature: Sampling temperature.
        
        Returns:
            LLMResponse with content and/or tool calls.
        """
        model = self._resolve_model(model or self.default_model)
        
        kwargs: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        
        # Apply model-specific overrides (e.g. kimi-k2.5 temperature)
        self._apply_model_overrides(model, kwargs)
        
        # Pass api_key directly — more reliable than env vars alone
        if self.api_key:
            kwargs["api_key"] = self.api_key
        
        # Pass api_base for custom endpoints
        if self.api_base:
            kwargs["api_base"] = self.api_base
        
        # Pass extra headers (e.g. APP-Code for AiHubMix)
        if self.extra_headers:
            kwargs["extra_headers"] = self.extra_headers
        
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"

        retried_without_tools = False
        try:
            response = await acompletion(**kwargs)
            return self._parse_response(response)
        except Exception as e:
            if self._can_retry_without_tools(e, tools_used=bool(tools)):
                retried_without_tools = True
                retry_kwargs = dict(kwargs)
                retry_kwargs.pop("tools", None)
                retry_kwargs.pop("tool_choice", None)
                try:
                    response = await acompletion(**retry_kwargs)
                    return self._parse_response(response)
                except Exception as retry_err:
                    e = retry_err
            # Return error as content for graceful handling
            return LLMResponse(
                content=self._format_error(
                    e,
                    context={
                        "provider_name": self.provider_name or "auto",
                        "api_base": self.api_base or "",
                        "model": model,
                        "tools_count": len(tools or []),
                        "retried_without_tools": retried_without_tools,
                    },
                ),
                finish_reason="error",
            )
    
    def _parse_response(self, response: Any) -> LLMResponse:
        """Parse LiteLLM response into our standard format."""
        choice = response.choices[0]
        message = choice.message
        
        tool_calls = []
        if hasattr(message, "tool_calls") and message.tool_calls:
            for tc in message.tool_calls:
                # Parse arguments from JSON string if needed
                args = tc.function.arguments
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except json.JSONDecodeError:
                        args = {"raw": args}
                
                tool_calls.append(ToolCallRequest(
                    id=tc.id,
                    name=tc.function.name,
                    arguments=args,
                ))
        
        usage = {}
        if hasattr(response, "usage") and response.usage:
            usage = {
                "prompt_tokens": response.usage.prompt_tokens,
                "completion_tokens": response.usage.completion_tokens,
                "total_tokens": response.usage.total_tokens,
            }
        
        reasoning_content = getattr(message, "reasoning_content", None)
        
        return LLMResponse(
            content=message.content,
            tool_calls=tool_calls,
            finish_reason=choice.finish_reason or "stop",
            usage=usage,
            reasoning_content=reasoning_content,
        )
    
    def get_default_model(self) -> str:
        """Get the default model."""
        return self.default_model
