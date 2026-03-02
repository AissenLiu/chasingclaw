"""Agent loop: the core processing engine."""

import asyncio
import datetime
import json
from pathlib import Path
from typing import Any

from loguru import logger

from chasingclaw.bus.events import InboundMessage, OutboundMessage
from chasingclaw.bus.queue import MessageBus
from chasingclaw.providers.base import LLMProvider
from chasingclaw.agent.context import ContextBuilder
from chasingclaw.agent.tools.registry import ToolRegistry
from chasingclaw.agent.tools.filesystem import ReadFileTool, WriteFileTool, EditFileTool, ListDirTool
from chasingclaw.agent.tools.shell import ExecTool
from chasingclaw.agent.tools.web import WebSearchTool, WebFetchTool
from chasingclaw.agent.tools.message import MessageTool
from chasingclaw.agent.tools.spawn import SpawnTool
from chasingclaw.agent.tools.cron import CronTool
from chasingclaw.agent.subagent import SubagentManager
from chasingclaw.session.manager import SessionManager


class AgentLoop:
    """
    The agent loop is the core processing engine.
    
    It:
    1. Receives messages from the bus
    2. Builds context with history, memory, skills
    3. Calls the LLM
    4. Executes tool calls
    5. Sends responses back
    """
    
    def __init__(
        self,
        bus: MessageBus,
        provider: LLMProvider,
        workspace: Path,
        model: str | None = None,
        max_iterations: int = 20,
        brave_api_key: str | None = None,
        exec_config: "ExecToolConfig | None" = None,
        cron_service: "CronService | None" = None,
        restrict_to_workspace: bool = False,
        session_manager: SessionManager | None = None,
    ):
        from chasingclaw.config.schema import ExecToolConfig
        from chasingclaw.cron.service import CronService
        self.bus = bus
        self.provider = provider
        self.workspace = workspace
        self.model = model or provider.get_default_model()
        self.max_iterations = max_iterations
        self.brave_api_key = brave_api_key
        self.exec_config = exec_config or ExecToolConfig()
        self.cron_service = cron_service
        self.restrict_to_workspace = restrict_to_workspace
        
        self.context = ContextBuilder(workspace)
        self.sessions = session_manager or SessionManager(workspace)
        self.tools = ToolRegistry()
        self.subagents = SubagentManager(
            provider=provider,
            workspace=workspace,
            bus=bus,
            model=self.model,
            brave_api_key=brave_api_key,
            exec_config=self.exec_config,
            restrict_to_workspace=restrict_to_workspace,
        )
        
        self._running = False
        self._register_default_tools()
    
    def _register_default_tools(self) -> None:
        """Register the default set of tools."""
        # File tools (restrict to workspace if configured)
        allowed_dir = self.workspace if self.restrict_to_workspace else None
        self.tools.register(ReadFileTool(allowed_dir=allowed_dir))
        self.tools.register(WriteFileTool(allowed_dir=allowed_dir))
        self.tools.register(EditFileTool(allowed_dir=allowed_dir))
        self.tools.register(ListDirTool(allowed_dir=allowed_dir))
        
        # Shell tool
        self.tools.register(ExecTool(
            working_dir=str(self.workspace),
            timeout=self.exec_config.timeout,
            restrict_to_workspace=self.restrict_to_workspace,
        ))
        
        # Web tools
        self.tools.register(WebSearchTool(api_key=self.brave_api_key))
        self.tools.register(WebFetchTool())
        
        # Message tool
        message_tool = MessageTool(send_callback=self.bus.publish_outbound)
        self.tools.register(message_tool)
        
        # Spawn tool (for subagents)
        spawn_tool = SpawnTool(manager=self.subagents)
        self.tools.register(spawn_tool)
        
        # Cron tool (for scheduling)
        if self.cron_service:
            self.tools.register(CronTool(self.cron_service))

    def _clip_trace_text(self, value: Any, limit: int = 1200) -> str:
        text = str(value).strip()
        if len(text) <= limit:
            return text
        return text[:limit] + "...(truncated)"
    
    async def run(self) -> None:
        """Run the agent loop, processing messages from the bus."""
        self._running = True
        logger.info("Agent loop started")
        
        while self._running:
            try:
                # Wait for next message
                msg = await asyncio.wait_for(
                    self.bus.consume_inbound(),
                    timeout=1.0
                )
                
                # Process it
                try:
                    response = await self._process_message(msg)
                    if response:
                        await self.bus.publish_outbound(response)
                except Exception as e:
                    logger.error(f"Error processing message: {e}")
                    # Send error response
                    await self.bus.publish_outbound(OutboundMessage(
                        channel=msg.channel,
                        chat_id=msg.chat_id,
                        content=f"Sorry, I encountered an error: {str(e)}"
                    ))
            except asyncio.TimeoutError:
                continue
    
    def stop(self) -> None:
        """Stop the agent loop."""
        self._running = False
        logger.info("Agent loop stopping")
    
    async def _process_message(self, msg: InboundMessage) -> OutboundMessage | None:
        """
        Process a single inbound message.
        
        Args:
            msg: The inbound message to process.
        
        Returns:
            The response message, or None if no response needed.
        """
        # Handle system messages (subagent announces)
        # The chat_id contains the original "channel:chat_id" to route back to
        if msg.channel == "system":
            return await self._process_system_message(msg)
        
        preview = msg.content[:80] + "..." if len(msg.content) > 80 else msg.content
        logger.info(f"Processing message from {msg.channel}:{msg.sender_id}: {preview}")
        
        # Get or create session
        session = self.sessions.get_or_create(msg.session_key)
        
        # Update tool contexts
        message_tool = self.tools.get("message")
        if isinstance(message_tool, MessageTool):
            message_tool.set_context(msg.channel, msg.chat_id)
        
        spawn_tool = self.tools.get("spawn")
        if isinstance(spawn_tool, SpawnTool):
            spawn_tool.set_context(msg.channel, msg.chat_id)
        
        cron_tool = self.tools.get("cron")
        if isinstance(cron_tool, CronTool):
            cron_tool.set_context(msg.channel, msg.chat_id)
        
        # Build initial messages (use get_history for LLM-formatted messages)
        messages = self.context.build_messages(
            history=session.get_history(),
            current_message=msg.content,
            media=msg.media if msg.media else None,
            channel=msg.channel,
            chat_id=msg.chat_id,
        )
        
        # Agent loop
        iteration = 0
        final_content = None
        trace_events: list[dict[str, Any]] = []
        
        while iteration < self.max_iterations:
            iteration += 1
            
            # Call LLM
            response = await self.provider.chat(
                messages=messages,
                tools=self.tools.get_definitions(),
                model=self.model
            )
            
            # Handle tool calls
            if response.has_tool_calls:
                trace_events.append(
                    {
                        "type": "tool_plan",
                        "iteration": iteration,
                        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                        "summary": f"第 {iteration} 轮：模型计划调用 {len(response.tool_calls)} 个工具",
                    }
                )
                # Add assistant message with tool calls
                tool_call_dicts = [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.name,
                            "arguments": json.dumps(tc.arguments)  # Must be JSON string
                        }
                    }
                    for tc in response.tool_calls
                ]
                messages = self.context.add_assistant_message(
                    messages, response.content, tool_call_dicts,
                    reasoning_content=response.reasoning_content,
                )
                
                # Execute tools
                for tool_call in response.tool_calls:
                    args_str = json.dumps(tool_call.arguments, ensure_ascii=False)
                    trace_events.append(
                        {
                            "type": "tool_call",
                            "iteration": iteration,
                            "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                            "tool": tool_call.name,
                            "callId": tool_call.id,
                            "arguments": self._clip_trace_text(args_str, limit=2000),
                            "summary": f"调用工具 {tool_call.name}",
                        }
                    )
                    logger.info(f"Tool call: {tool_call.name}({args_str[:200]})")
                    result = await self.tools.execute(tool_call.name, tool_call.arguments)
                    is_error = str(result).startswith("Error")
                    trace_events.append(
                        {
                            "type": "tool_result",
                            "iteration": iteration,
                            "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                            "tool": tool_call.name,
                            "callId": tool_call.id,
                            "status": "error" if is_error else "ok",
                            "result": self._clip_trace_text(result, limit=3000),
                            "summary": f"{tool_call.name} 执行{'失败' if is_error else '完成'}",
                        }
                    )
                    messages = self.context.add_tool_result(
                        messages, tool_call.id, tool_call.name, result
                    )
            else:
                # No tool calls, we're done
                trace_events.append(
                    {
                        "type": "assistant_final",
                        "iteration": iteration,
                        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                        "summary": f"第 {iteration} 轮：模型直接返回最终回答",
                    }
                )
                final_content = response.content
                break
        
        if final_content is None:
            final_content = "I've completed processing but have no response to give."
        
        # Log response preview
        preview = final_content[:120] + "..." if len(final_content) > 120 else final_content
        logger.info(f"Response to {msg.channel}:{msg.sender_id}: {preview}")
        
        # Save to session. For Web UI, allow a shorter display message while keeping
        # full prompt payload in LLM context.
        display_content = str(msg.metadata.get("displayContent") or msg.content)
        attachments = msg.metadata.get("attachments")
        user_kwargs: dict[str, Any] = {}
        if isinstance(attachments, list) and attachments:
            user_kwargs["attachments"] = attachments
        session.add_message("user", display_content, **user_kwargs)
        session.add_message("assistant", final_content, trace=trace_events)
        self.sessions.save(session)
        
        outbound_metadata = dict(msg.metadata or {})
        outbound_metadata["trace"] = trace_events
        if attachments:
            outbound_metadata["attachments"] = attachments

        return OutboundMessage(
            channel=msg.channel,
            chat_id=msg.chat_id,
            content=final_content,
            metadata=outbound_metadata,  # Keep channel metadata and tool execution trace.
        )
    
    async def _process_system_message(self, msg: InboundMessage) -> OutboundMessage | None:
        """
        Process a system message (e.g., subagent announce).
        
        The chat_id field contains "original_channel:original_chat_id" to route
        the response back to the correct destination.
        """
        logger.info(f"Processing system message from {msg.sender_id}")
        
        # Parse origin from chat_id (format: "channel:chat_id")
        if ":" in msg.chat_id:
            parts = msg.chat_id.split(":", 1)
            origin_channel = parts[0]
            origin_chat_id = parts[1]
        else:
            # Fallback
            origin_channel = "cli"
            origin_chat_id = msg.chat_id
        
        # Use the origin session for context
        session_key = f"{origin_channel}:{origin_chat_id}"
        session = self.sessions.get_or_create(session_key)
        
        # Update tool contexts
        message_tool = self.tools.get("message")
        if isinstance(message_tool, MessageTool):
            message_tool.set_context(origin_channel, origin_chat_id)
        
        spawn_tool = self.tools.get("spawn")
        if isinstance(spawn_tool, SpawnTool):
            spawn_tool.set_context(origin_channel, origin_chat_id)
        
        cron_tool = self.tools.get("cron")
        if isinstance(cron_tool, CronTool):
            cron_tool.set_context(origin_channel, origin_chat_id)
        
        # Build messages with the announce content
        messages = self.context.build_messages(
            history=session.get_history(),
            current_message=msg.content,
            channel=origin_channel,
            chat_id=origin_chat_id,
        )
        
        # Agent loop (limited for announce handling)
        iteration = 0
        final_content = None
        
        while iteration < self.max_iterations:
            iteration += 1
            
            response = await self.provider.chat(
                messages=messages,
                tools=self.tools.get_definitions(),
                model=self.model
            )
            
            if response.has_tool_calls:
                tool_call_dicts = [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.name,
                            "arguments": json.dumps(tc.arguments)
                        }
                    }
                    for tc in response.tool_calls
                ]
                messages = self.context.add_assistant_message(
                    messages, response.content, tool_call_dicts,
                    reasoning_content=response.reasoning_content,
                )
                
                for tool_call in response.tool_calls:
                    args_str = json.dumps(tool_call.arguments, ensure_ascii=False)
                    logger.info(f"Tool call: {tool_call.name}({args_str[:200]})")
                    result = await self.tools.execute(tool_call.name, tool_call.arguments)
                    messages = self.context.add_tool_result(
                        messages, tool_call.id, tool_call.name, result
                    )
            else:
                final_content = response.content
                break
        
        if final_content is None:
            final_content = "Background task completed."
        
        # Save to session (mark as system message in history)
        session.add_message("user", f"[System: {msg.sender_id}] {msg.content}")
        session.add_message("assistant", final_content)
        self.sessions.save(session)
        
        return OutboundMessage(
            channel=origin_channel,
            chat_id=origin_chat_id,
            content=final_content
        )
    
    async def process_direct(
        self,
        content: str,
        session_key: str = "cli:direct",
        channel: str = "cli",
        chat_id: str = "direct",
        metadata: dict[str, Any] | None = None,
    ) -> str:
        """
        Process a message directly (for CLI or cron usage).
        
        Args:
            content: The message content.
            session_key: Session identifier.
            channel: Source channel (for context).
            chat_id: Source chat ID (for context).
        
        Returns:
            The agent's response.
        """
        if session_key and ":" in session_key:
            sk_channel, sk_chat_id = session_key.split(":", 1)
            channel = sk_channel or channel
            chat_id = sk_chat_id or chat_id

        msg = InboundMessage(
            channel=channel,
            sender_id="user",
            chat_id=chat_id,
            content=content,
            metadata=metadata or {},
        )
        
        response = await self._process_message(msg)
        return response.content if response else ""

    async def process_direct_with_result(
        self,
        content: str,
        session_key: str = "cli:direct",
        channel: str = "cli",
        chat_id: str = "direct",
        metadata: dict[str, Any] | None = None,
    ) -> OutboundMessage | None:
        """Process a direct message and return the full outbound payload."""
        if session_key and ":" in session_key:
            sk_channel, sk_chat_id = session_key.split(":", 1)
            channel = sk_channel or channel
            chat_id = sk_chat_id or chat_id

        msg = InboundMessage(
            channel=channel,
            sender_id="user",
            chat_id=chat_id,
            content=content,
            metadata=metadata or {},
        )
        return await self._process_message(msg)

    async def process_direct_streaming(
        self,
        content: str,
        session_key: str = "webui:direct",
        channel: str = "webui",
        chat_id: str = "direct",
        metadata: dict[str, Any] | None = None,
    ):
        """
        Process a direct message with streaming output.
        Yields dicts:
          {"type": "tool_call",   "tool": ..., "callId": ..., "arguments": ...}
          {"type": "tool_result", "tool": ..., "callId": ..., "status": "ok"|"error", "result": ...}
          {"type": "token",       "text": ...}
          {"type": "done",        "reply": ..., "trace": [...]}
        """
        if session_key and ":" in session_key:
            sk_channel, sk_chat_id = session_key.split(":", 1)
            channel = sk_channel or channel
            chat_id = sk_chat_id or chat_id

        session = self.sessions.get_or_create(session_key)

        # Update tool contexts
        for tool_name, ctx_channel, ctx_chat_id in [
            ("message", channel, chat_id),
            ("spawn", channel, chat_id),
            ("cron", channel, chat_id),
        ]:
            t = self.tools.get(tool_name)
            if t and hasattr(t, "set_context"):
                t.set_context(ctx_channel, ctx_chat_id)

        msg = InboundMessage(
            channel=channel,
            sender_id="user",
            chat_id=chat_id,
            content=content,
            metadata=metadata or {},
        )

        messages = self.context.build_messages(
            history=session.get_history(),
            current_message=msg.content,
            media=msg.media if hasattr(msg, "media") and msg.media else None,
            channel=channel,
            chat_id=chat_id,
        )

        iteration = 0
        final_content = None
        trace_events: list[dict[str, Any]] = []
        has_stream = hasattr(self.provider, "chat_stream")

        while iteration < self.max_iterations:
            iteration += 1

            if has_stream:
                # --- streaming call ---
                llm_response: LLMResponse | None = None
                content_buf: list[str] = []

                async for item in self.provider.chat_stream(
                    messages=messages,
                    tools=self.tools.get_definitions(),
                    model=self.model,
                ):
                    if isinstance(item, str):
                        content_buf.append(item)
                        yield {"type": "token", "text": item}
                    else:
                        llm_response = item

                if llm_response is None:
                    llm_response = LLMResponse(content="".join(content_buf) or None)
            else:
                # Fallback: non-streaming
                llm_response = await self.provider.chat(
                    messages=messages,
                    tools=self.tools.get_definitions(),
                    model=self.model,
                )

            if llm_response.has_tool_calls:
                trace_events.append({
                    "type": "tool_plan",
                    "iteration": iteration,
                    "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                    "summary": f"第 {iteration} 轮：模型计划调用 {len(llm_response.tool_calls)} 个工具",
                })
                tool_call_dicts = [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.name,
                            "arguments": json.dumps(tc.arguments),
                        },
                    }
                    for tc in llm_response.tool_calls
                ]
                messages = self.context.add_assistant_message(
                    messages, llm_response.content, tool_call_dicts,
                    reasoning_content=llm_response.reasoning_content,
                )

                for tool_call in llm_response.tool_calls:
                    args_str = json.dumps(tool_call.arguments, ensure_ascii=False)
                    trace_event = {
                        "type": "tool_call",
                        "iteration": iteration,
                        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                        "tool": tool_call.name,
                        "callId": tool_call.id,
                        "arguments": self._clip_trace_text(args_str, limit=2000),
                        "summary": f"调用工具 {tool_call.name}",
                    }
                    trace_events.append(trace_event)
                    yield {
                        "type": "tool_call",
                        "tool": tool_call.name,
                        "callId": tool_call.id,
                        "arguments": self._clip_trace_text(args_str, limit=500),
                        "summary": f"调用工具 {tool_call.name}",
                    }

                    logger.info(f"Tool call: {tool_call.name}({args_str[:200]})")
                    result = await self.tools.execute(tool_call.name, tool_call.arguments)
                    is_error = str(result).startswith("Error")

                    result_event = {
                        "type": "tool_result",
                        "iteration": iteration,
                        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                        "tool": tool_call.name,
                        "callId": tool_call.id,
                        "status": "error" if is_error else "ok",
                        "result": self._clip_trace_text(result, limit=3000),
                        "summary": f"{tool_call.name} 执行{'失败' if is_error else '完成'}",
                    }
                    trace_events.append(result_event)
                    yield {
                        "type": "tool_result",
                        "tool": tool_call.name,
                        "callId": tool_call.id,
                        "status": "error" if is_error else "ok",
                        "result": self._clip_trace_text(result, limit=500),
                    }
                    messages = self.context.add_tool_result(
                        messages, tool_call.id, tool_call.name, result
                    )
            else:
                trace_events.append({
                    "type": "assistant_final",
                    "iteration": iteration,
                    "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                    "summary": f"第 {iteration} 轮：模型直接返回最终回答",
                })
                final_content = llm_response.content
                break

        if final_content is None:
            final_content = "I've completed processing but have no response to give."

        # Save session
        display_content = str((metadata or {}).get("displayContent") or content)
        attachments = (metadata or {}).get("attachments")
        user_kwargs: dict[str, Any] = {}
        if isinstance(attachments, list) and attachments:
            user_kwargs["attachments"] = attachments
        session.add_message("user", display_content, **user_kwargs)
        session.add_message("assistant", final_content, trace=trace_events)
        self.sessions.save(session)

        yield {"type": "done", "reply": final_content, "trace": trace_events}

