"""Laya Browser Agent: Zero-cost autonomous web agent using Laya MLX decision models."""

from .laya_engine import LayaEngine
from .extractor import SlotExtractor
from .browser import PlaywrightWebMcpDriver
from .agent import LayaAgent

__version__ = "0.1.0"
__all__ = ["LayaEngine", "SlotExtractor", "PlaywrightWebMcpDriver", "LayaAgent"]
