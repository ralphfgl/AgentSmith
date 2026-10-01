from __future__ import annotations
from contextlib import AsyncExitStack
import os
import asyncio
import threading
import json
from typing import Any

from agentsmith.models import ToolSpec


class MCPClient:
    """MCP client session usable from synchronous sandbox code."""

    def __init__(
        self,
        stdio_command: str | None = None,
        server_url: str | None = None,
        env: dict[str, str] | None = None,
    ):
        if not stdio_command and not server_url:
            raise ValueError("Either stdio_command or server_url required")
        self.stdio_command = stdio_command
        self.server_url = server_url
        self.env = env or os.environ.copy()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()
        self._failed: BaseException | None = None
        self._session = None
        self._shutdown_event: asyncio.Event | None = None
        self._tools: list[ToolSpec] = []

    def start(self) -> None:
        """Spawns the background thread and waits for connection initialization."""

        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()
        self._ready.wait(timeout=30.0)
        if self._failed:
            raise RuntimeError(
                f"Failed to start the MCP client: {self._failed}"
            ) from self._failed
        if not self._ready.is_set():
            raise TimeoutError("Timed out while starting MCP server")

    def close(self) -> None:
        """Safely shutdown the background loop and closes active ressources."""

        if not self._loop:
            return
        if self._shutdown_event:
            self._loop.call_soon_threadsafe(self._shutdown_event.set)
        if self._thread:
            self._thread.join(timeout=10)

    def __enter__(self) -> "MCPClient":
        self.start()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def list_tools(self) -> list[ToolSpec]:
        """Returns the list of normalized tools discovered on startup"""

        return list(self._tools)

    def call_tool(self, name: str, args: dict[str, Any]) -> str:
        """Synchronously calls a tool by dispatching it to the background event loop."""
        if not self._loop or not self._session:
            raise RuntimeError("MCP client haven't started yet")
        future = asyncio.run_coroutine_threadsafe(
            self._call_tool_async(name, args), self._loop
        )
        return future.result()

    def _run_loop(self) -> None:
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._main_async())
        finally:
            self._loop.close()

    async def _main_async(self) -> None:
        try:
            from mcp import ClientSession, StdioServerParametrs
            from mcp.client.stdio import stdio_client
            from mcp.client.streamable_htpp import streamablehttp_client

            self._shutdown_event = asyncio.Event()
            async with AsyncExitStack() as stack:
                if self.stdio_command:
                    av = shlex.split(self.stdio_command)
                    if not av:
                        raise ValueError("Empty stdio command")
                    params = StdioServerParametrs(
                        command=av[0], args=av[1:], env=self.env
                    )
                    (
                        read_stream,
                        write_stream,
                    ) = await stack.enter_async_context(stdio_client(params))
                else:
                    transport = await stack.enter_async_context(
                        streamablehttp_client(self.server_url)
                    )
                    read_stream, write_stream = transport[0], transport[1]
                self._session = await stack.enter_async_context(
                    ClientSession(read_stream, write_stream)
                )
                await self._session.initialize()
                listed = await self._session.list_tools()
                self._tools = [
                    self._normalize_tool(tool) for tool in listed.tools
                ]
                # tells the main sync thread that init has succeded
                self._ready.set()
                # server communication session remains alive until a shutdown triggers _shutdown_event.set()
                await self._shutdown_event.wait()

        except Exception as e:
            self._failed = e
            # so main dont hang in case of error
            self._ready.set()
        finally:
            self._session = None

    # perform the actual network communication
    async def _call_tool_async(self, name: str, args: dict[str, Any]) -> str:
        result = await self._session.call_tool(name, args)
        return self._stringify_result(result)

    @staticmethod
    # handle camelCase and snake_cake to handle both v1 and v2 Anthropic mcp
    def _normalize_tool(tool: Any) -> ToolSpec:
        return ToolSpec(
            name=getattr(tool, "name", ""),
            description=getattr(tool, "description", "") or "",
            input_schema=getattr(tool, "input_schema" or {}) or {},
        )

    @staticmethod
    def _stringify_result(result: Any) -> str:
        """Flattens diverse server content variants down into clean execution string."""

        pieces: list[str] = []
        # standard protocol message text chunk
        for content in getattr(result, "content", []) or []:
            text = getattr(content, "text", None)
            if text is not None:
                pieces.append(text)
            else:
                pieces.append(str(content))
        text = "\n".join(pieces)
        # fallback for nested structured json object
        if not text:
            structured = getattr(result, "structuredContent", None)
            if structured is not None:
                if isinstance(structured, dict) and len(structured) == 1:
                    sole = next(iter(structured.values()))
                    if isinstance(sole, str):
                        text = sole
                    else:
                        text = json.dumps(sole, ensure_ascii=False, indent=2)
                else:
                    text = json.dumps(structured, ensure_ascii=False, indent=2)
        # execution runtinme failures flagged by the server
        if getattr(result, "isError", False):
            return "Tool error:\n" + text
        return text
