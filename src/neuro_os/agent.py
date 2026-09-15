"""Core agent loop for NeuroOS."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel

from neuro_os.config import settings


class AgentError(RuntimeError):
    """Base error for failures that prevent an agent from producing a result."""


class AgentConfigurationError(AgentError):
    """Raised when the configured model provider cannot be used."""


class AgentProviderError(AgentError):
    """Raised when the model provider rejects or fails a request."""


class ToolCall(BaseModel):
    """Represents a tool call from the LLM."""

    name: str
    arguments: dict
    id: str


class ToolResult(BaseModel):
    """Result of a tool execution."""

    tool_call_id: str
    name: str
    result: Any
    error: str | None = None


class AgentMessage(BaseModel):
    """Message in the agent conversation."""

    role: str  # user, assistant, tool, system
    content: str | None = None
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_call_id: str | None = None
    name: str | None = None


@dataclass
class AgentContext:
    """Context passed through the agent loop."""

    user_id: UUID
    session_id: UUID
    current_task_id: UUID | None = None
    protocol_id: UUID | None = None
    working_memory: dict = field(default_factory=dict)
    long_term_memory: list[dict] = field(default_factory=list)
    energy_level: str = "shallow"
    timestamp: datetime = field(default_factory=datetime.utcnow)


class BaseTool:
    """Base class for all tools."""

    name: str
    description: str
    parameters: dict  # JSON schema

    async def execute(self, arguments: dict, context: AgentContext) -> ToolResult:
        raise NotImplementedError


class Agent:
    """Main agent loop with tool calling and memory."""

    def __init__(
        self,
        tools: list[BaseTool],
        system_prompt: str,
        model: str = None,
        max_iterations: int = 10,
    ):
        self.tools = {t.name: t for t in tools}
        self.system_prompt = system_prompt
        self.provider = "openai"
        self.model = model or settings.default_model
        self.max_iterations = max_iterations
        self._tool_schemas = [self._tool_to_schema(t) for t in tools]
        self._llm_client = None

    def _get_llm_client(self):
        """Lazy init LLM client."""
        if self._llm_client is None:
            from openai import AsyncOpenAI

            if settings.openai_api_key:
                self._llm_client = AsyncOpenAI(api_key=settings.openai_api_key)
            else:
                raise AgentConfigurationError(
                    "OPENAI_API_KEY is required to run AI-backed protocols"
                )
        return self._llm_client

    def _tool_to_schema(self, tool: BaseTool) -> dict:
        return {
            "type": "function",
            "function": {
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.parameters,
            },
        }

    async def run(
        self,
        user_input: str,
        context: AgentContext,
        on_tool_call: Callable[[ToolCall], None] | None = None,
        on_tool_result: Callable[[ToolResult], None] | None = None,
    ) -> str:
        """Run the agent loop."""
        messages = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": user_input},
        ]

        for _ in range(self.max_iterations):
            response = await self._call_llm(messages, context)

            tool_calls = response.get("tool_calls", [])
            if not tool_calls:
                content = response.get("content")
                if not content:
                    raise AgentProviderError("Model returned no content")
                return content

            messages.append(
                {
                    "role": "assistant",
                    "content": response.get("content"),
                    "tool_calls": [
                        {
                            "id": tool_call.id,
                            "type": "function",
                            "function": {
                                "name": tool_call.name,
                                "arguments": json.dumps(tool_call.arguments),
                            },
                        }
                        for tool_call in tool_calls
                    ],
                }
            )

            for tc in tool_calls:
                if on_tool_call:
                    on_tool_call(tc)

                tool = self.tools.get(tc.name)
                if not tool:
                    result = ToolResult(
                        tool_call_id=tc.id,
                        name=tc.name,
                        result=None,
                        error=f"Unknown tool: {tc.name}",
                    )
                else:
                    try:
                        result = await tool.execute(tc.arguments, context)
                        result.tool_call_id = tc.id
                    except Exception as e:
                        result = ToolResult(
                            tool_call_id=tc.id,
                            name=tc.name,
                            result=None,
                            error=str(e),
                        )

                if on_tool_result:
                    on_tool_result(result)

                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "name": tc.name,
                        "content": json.dumps({"result": result.result, "error": result.error}),
                    }
                )

        raise AgentError(f"Agent exceeded its {self.max_iterations}-iteration limit")

    async def _call_llm(self, messages: list[dict], context: AgentContext) -> dict:
        """Call the LLM provider with tool schemas."""
        client = self._get_llm_client()

        # Use response_format for structured output when no tools
        if not self._tool_schemas:
            try:
                response = await client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    response_format={"type": "json_object"},
                    temperature=0.3,
                )
                content = response.choices[0].message.content
                return {"content": content, "tool_calls": []}
            except Exception as e:
                raise AgentProviderError(f"Model request failed: {e}") from e

        # With tools - let the model decide
        try:
            response = await client.chat.completions.create(
                model=self.model,
                messages=messages,
                tools=self._tool_schemas,
                tool_choice="auto",
                temperature=0.3,
            )
            message = response.choices[0].message

            if message.tool_calls:
                tool_calls: list[ToolCall] = []
                for tc in message.tool_calls:
                    try:
                        arguments = json.loads(tc.function.arguments or "{}")
                    except json.JSONDecodeError as e:
                        raise AgentProviderError(
                            f"Model returned invalid arguments for {tc.function.name}"
                        ) from e
                    if not isinstance(arguments, dict):
                        raise AgentProviderError(
                            f"Model returned non-object arguments for {tc.function.name}"
                        )
                    tool_calls.append(
                        ToolCall(name=tc.function.name, arguments=arguments, id=tc.id)
                    )
                return {"content": message.content, "tool_calls": tool_calls}
            return {"content": message.content, "tool_calls": []}
        except Exception as e:
            if isinstance(e, AgentError):
                raise
            raise AgentProviderError(f"Model request failed: {e}") from e


# Protocol-specific agents
MORNING_PROTOCOL_PROMPT = """You are the Morning Protocol agent for NeuroOS.
Your job: take the user's calendar, inbox, open loops, and energy profile → output today's 3 sequenced work blocks with energy labels.
Be concise. No fluff. Return JSON with: blocks[title, energy_level, estimated_minutes, tasks[]]."""

INTERRUPTION_RECOVERY_PROMPT = """You are the Interruption Recovery agent.
Input: context_snapshot (editor position, mental stack, next micro-step), interruption duration.
Output: exact next step to resume flow. One sentence. No questions."""

SHUTDOWN_PROTOCOL_PROMPT = """You are the Shutdown Protocol agent.
Input: today's completed tasks, open loops, tomorrow's calendar.
Output: 3 items for tomorrow's morning protocol + 1 personal note. JSON format."""

COMMS_DRAFT_PROMPT = """You are the Comms Drafter.
Input: recipient, channel, goal, constraints, user's voice samples.
Output: draft message ready to review. Match user's tone. No markdown unless asked."""
