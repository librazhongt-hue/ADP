"""Unified LLM invocation gateway and prompt registry.

See docs/06-LLMGateway设计.md.
"""

from adp.llm.contracts import (
    LLMChunk,
    LLMMessage,
    LLMRequest,
    LLMResponse,
    ToolCallRequest,
    ToolSchema,
)
from adp.llm.gateway import LLMGateway, LLMGatewayError
from adp.llm.prompts import FilePromptSource, PromptRegistry, PromptSource, PromptTemplate
from adp.llm.provider import LLMProvider, OpenAICompatibleProvider

__all__ = [
    "LLMMessage",
    "ToolSchema",
    "LLMRequest",
    "ToolCallRequest",
    "LLMResponse",
    "LLMChunk",
    "LLMProvider",
    "OpenAICompatibleProvider",
    "LLMGateway",
    "LLMGatewayError",
    "PromptTemplate",
    "PromptSource",
    "FilePromptSource",
    "PromptRegistry",
]
