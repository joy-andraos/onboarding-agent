import os
import re
import sys
import logging
import traceback
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

from anthropic import AsyncAnthropic
from anthropic.types import MessageParam, TextBlock, ToolParam, ToolResultBlockParam
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from dotenv import load_dotenv

load_dotenv()

SERVER_SCRIPT = Path("mcp_server/server.py")
SKILL_PATH = Path("skills/onboarding-rules/SKILL.md")

MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-5")
MAX_AGENT_TURNS = 8

GITHUB_URL_RE = re.compile(
    r"github\.com/(?P<owner>[\w.-]+)/(?P<repo>[\w.-]+?)(?:\.git)?(?:/tree/(?P<branch>[\w./-]+))?/?$"
)

logger = logging.getLogger("uvicorn.error")


class InvalidRepoUrl(ValueError):
    pass


def parse_github_url(url: str) -> tuple[str, str, str | None]:
    url = url.strip()
    match = GITHUB_URL_RE.search(url)
    if not match:
        raise InvalidRepoUrl(
            f"Couldn't parse a GitHub owner/repo out of: {url!r}. "
            f"Expected something like https://github.com/owner/repo"
        )
    return match.group("owner"), match.group("repo"), match.group("branch")


def _load_skill_system_prompt() -> str:
    skill_text = SKILL_PATH.read_text(encoding="utf-8")
    return (
        "You are an onboarding assistant. Follow the skill instructions below "
        "exactly. In particular, always use the exact output structure it "
        "specifies. You have tools available to fetch real repo data; use them "
        "before writing anything, and ground every claim in what the tools "
        "actually returned.\n\n"
        f"{skill_text}"
    )


def _mcp_tools_to_anthropic_format(mcp_tools) -> list[ToolParam]:
    return [
        {
            "name": tool.name,
            "description": tool.description or "",
            "input_schema": tool.inputSchema,
        }
        for tool in mcp_tools
    ]


@asynccontextmanager
async def _mcp_session():
    server_params = StdioServerParameters(
        command=sys.executable,
        args=[str(SERVER_SCRIPT)],
        env={**os.environ},
    )
    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            yield session


async def stream_onboarding_doc(repo_url: str) -> AsyncIterator[dict]:
    """Yields event dicts as the agent works. Event types:
    status, tool_call, tool_result, text_delta, done, error.
    """
    owner, repo, branch = parse_github_url(repo_url)
    branch_hint = f" on branch '{branch}'" if branch else ""

    client = AsyncAnthropic()
    system_prompt = _load_skill_system_prompt()

    user_message = (
        f"Onboard me onto the GitHub repo {owner}/{repo}{branch_hint}. "
        f"Use the available tools to gather the file structure and README, "
        f"then produce the onboarding doc following the skill's exact structure."
    )

    yield {"type": "status", "message": "connecting to mcp server"}

    try:
        async with _mcp_session() as session:
            yield {"type": "status", "message": "listing available tools"}
            tools_result = await session.list_tools()
            tools = _mcp_tools_to_anthropic_format(tools_result.tools)

            messages: list[MessageParam] = [{"role": "user", "content": user_message}]

            for _ in range(MAX_AGENT_TURNS):
                async with client.messages.stream(
                    model=MODEL,
                    max_tokens=4096,
                    system=system_prompt,
                    tools=tools,
                    messages=messages,
                ) as stream:
                    async for text in stream.text_stream:
                        yield {"type": "text_delta", "text": text}
                    response = await stream.get_final_message()

                messages.append({"role": "assistant", "content": response.content})

                if response.stop_reason != "tool_use":
                    text_blocks: list[TextBlock] = [
                        b for b in response.content if b.type == "text"
                    ]
                    final_doc = "\n".join(b.text for b in text_blocks)
                    yield {"type": "done", "doc": final_doc}
                    return

                tool_results: list[ToolResultBlockParam] = []
                for block in response.content:
                    if block.type != "tool_use":
                        continue

                    yield {"type": "tool_call", "name": block.name, "input": block.input}

                    tool_input = block.input if isinstance(block.input, dict) else {}
                    result = await session.call_tool(block.name, tool_input)

                    result_text = "\n".join(
                        part.text for part in result.content if part.type == "text"
                    )
                    is_error = getattr(result, "isError", False) or False

                    yield {"type": "tool_result", "name": block.name, "is_error": is_error}

                    tool_results.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": result_text,
                            "is_error": is_error,
                        }
                    )

                messages.append({"role": "user", "content": tool_results})

            yield {
                "type": "error",
                "detail": f"Agent didn't finish within {MAX_AGENT_TURNS} turns. The tool-use loop may be stuck.",
            }

    except* Exception as eg:
        for sub in eg.exceptions:
            logger.error("MCP session failed:")
            traceback.print_exception(type(sub), sub, sub.__traceback__)
        detail = str(eg.exceptions[0]) if eg.exceptions else str(eg)
        yield {"type": "error", "detail": detail}