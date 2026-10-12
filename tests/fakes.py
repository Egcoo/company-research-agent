"""Provide deterministic tool-calling model responses for offline graph tests."""

from typing import Any

from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.utils.function_calling import convert_to_openai_tool
from pydantic import Field


class ToolCallingFakeModel(FakeMessagesListChatModel):
    """Consume each scripted response once and fail on unexpected model calls."""

    responses: list[Any]
    bound_tools: list[dict] = Field(default_factory=list)
    bound_model_settings: dict[str, Any] = Field(default_factory=dict)

    def bind_tools(self, tools, *, tool_choice=None, **kwargs):
        """Accept the agent's tool definitions without constructing a provider."""
        self.bound_tools = [convert_to_openai_tool(tool) for tool in tools]
        self.bound_model_settings = dict(kwargs)
        return self

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        """Reject calls beyond the fixed script instead of cycling responses."""
        if self.i >= len(self.responses):
            raise AssertionError("Fake model response script exhausted.")
        response = self.responses[self.i]
        self.i += 1
        if callable(response):
            response = response(messages)
        return ChatResult(generations=[ChatGeneration(message=response)])


def call_tool(name: str, arguments: dict) -> AIMessage:
    """Construct one scripted model decision; execute its tool in the real graph."""
    return AIMessage(
        content="", tool_calls=[{"name": name, "args": arguments, "id": name}]
    )
