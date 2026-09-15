"""Tests for the agent execution loop."""

from uuid import uuid4

import pytest

from neuro_os.agent import Agent, AgentContext, BaseTool, ToolCall, ToolResult


class EchoTool(BaseTool):
    name = "echo"
    description = "Echo a value"
    parameters = {
        "type": "object",
        "properties": {"value": {"type": "string"}},
        "required": ["value"],
    }

    async def execute(self, arguments: dict, context: AgentContext) -> ToolResult:
        return ToolResult(
            tool_call_id="",
            name=self.name,
            result={"value": arguments["value"]},
        )


class ScriptedAgent(Agent):
    def __init__(self) -> None:
        super().__init__([EchoTool()], "test", max_iterations=2)
        self.requests: list[list[dict]] = []

    async def _call_llm(self, messages: list[dict], context: AgentContext) -> dict:
        self.requests.append(list(messages))
        if len(self.requests) == 1:
            return {
                "content": None,
                "tool_calls": [ToolCall(name="echo", arguments={"value": "grounded"}, id="call-1")],
            }
        return {"content": '{"answer":"done"}', "tool_calls": []}


@pytest.mark.asyncio
async def test_agent_executes_typed_tool_call_and_returns_final_content():
    agent = ScriptedAgent()
    context = AgentContext(user_id=uuid4(), session_id=uuid4())

    result = await agent.run("work", context)

    assert result == '{"answer":"done"}'
    second_request = agent.requests[1]
    assert second_request[2]["role"] == "assistant"
    assert second_request[2]["tool_calls"][0]["id"] == "call-1"
    assert second_request[3]["role"] == "tool"
    assert second_request[3]["tool_call_id"] == "call-1"
