"""Core agent loop for NeuroOS."""

from __future__ import annotations
import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Optional
from uuid import UUID

from pydantic import BaseModel, Field

from neuro_os.config import settings


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
    error: Optional[str] = None


class AgentMessage(BaseModel):
    """Message in the agent conversation."""
    role: str  # user, assistant, tool, system
    content: Optional[str] = None
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_call_id: Optional[str] = None
    name: Optional[str] = None


@dataclass
class AgentContext:
    """Context passed through the agent loop."""
    user_id: UUID
    session_id: UUID
    current_task_id: Optional[UUID] = None
    protocol_id: Optional[UUID] = None
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
        self.model = model or settings.default_model
        self.max_iterations = max_iterations
        self._tool_schemas = [self._tool_to_schema(t) for t in tools]

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
        on_tool_call: Optional[Callable[[ToolCall], None]] = None,
        on_tool_result: Optional[Callable[[ToolResult], None]] = None,
    ) -> str:
        """Run the agent loop."""
        messages = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": user_input},
        ]

        for iteration in range(self.max_iterations):
            # Call LLM (placeholder - integrate with your provider)
            response = await self._call_llm(messages, context)

            # Check for tool calls
            tool_calls = response.get("tool_calls", [])
            if not tool_calls:
                return response.get("content", "")

            # Execute tools
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
                    except Exception as e:
                        result = ToolResult(
                            tool_call_id=tc.id,
                            name=tc.name,
                            result=None,
                            error=str(e),
                        )

                if on_tool_result:
                    on_tool_result(result)

                # Add tool result to messages
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "name": tc.name,
                    "content": json.dumps({"result": result.result, "error": result.error}),
                })

        return "Max iterations reached"

    async def _call_llm(self, messages: list[dict], context: AgentContext) -> dict:
        """Call the LLM provider. Override with actual implementation."""
        # This is where you'd integrate with OpenAI, Anthropic, etc.
        # For now, return a mock response
        return {"content": "I'll help you with that.", "tool_calls": []}


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
