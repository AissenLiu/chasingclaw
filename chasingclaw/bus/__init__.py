"""Message bus module for decoupled channel-agent communication."""

from chasingclaw.bus.events import InboundMessage, OutboundMessage
from chasingclaw.bus.queue import MessageBus

__all__ = ["MessageBus", "InboundMessage", "OutboundMessage"]
