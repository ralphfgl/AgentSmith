"""MCP server exposing mandatory SWE-bench tools running inside a Host/Container sandbox."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

# Fix the import path to match your exact directory structure
from src.tools import HostBackend


# Define a simple context structure to hold configuration since we don't have SWEBenchToolContext
class SWEBenchToolContext:
    def __init__(
        self, task_file: Path | None, backend: HostBackend, eval_script: str
    ):
        self.task_file = task_file
        self.backend = backend
        self.eval_script = eval_script

    # Forward the tool calls directly to your HostBackend implementation
    def read_file(self, *args, **kwargs):
        return self.backend.read_file(*args, **kwargs)

    def edit_file(self, *args, **kwargs):
        return self.backend.edit_file(*args, **kwargs)

    def list_files(self, *args, **kwargs):
        return self.backend.list_files(*args, **kwargs)

    def search_code(self, *args, **kwargs):
        return self.backend.search_code(*args, **kwargs)

    def search_definition(self, *args, **kwargs):
        return self.backend.search_definition(*args, **kwargs)

    def find_references(self, *args, **kwargs):
        return self.backend.find_references(*args, **kwargs)

    def run_command(self, *args, **kwargs):
        return self.backend.run_command(*args, **kwargs)

    def get_patch(self, *args, **kwargs):
        return self.backend.get_patch(*args, **kwargs)

    def run_tests(self, timeout: int = 300) -> str:
        return self.backend.run_tests(
            eval_script=self.eval_script, timeout=timeout
        )


ctx: SWEBenchToolContext | None = None


def context() -> SWEBenchToolContext:
    """Lazy-loads the context based on your exact layout."""
    global ctx
    if ctx is None:
        # 1. Parse the task file if it exists
        task_file_value = os.environ.get("AGENT_SMITH_TASK_FILE")
        task_file = Path(task_file_value) if task_file_value else None
        task: dict = {}
        if task_file and task_file.exists():
            task = json.loads(task_file.read_text())

        # 2. Determine the path to the codebase workspace inside this container
        root_value = os.environ.get("AGENT_SMITH_TESTBED_PATH")
        workspace_path = Path(root_value) if root_value else Path("/testbed")

        # 3. Instantiate your HostBackend
        backend = HostBackend(workspace_path)

        # 4. Instantiate the wrapper context
        ctx = SWEBenchToolContext(
            task_file=task_file,
            backend=backend,
            eval_script=task.get("eval_script", ""),
        )
    return ctx


def build_server():
    # MCP v2 Migration: Import MCPServer instead of FastMCP
    from mcp.server.mcpserver import MCPServer

    mcp = MCPServer("agent-smith-swebench")

    @mcp.tool()
    def read_file(
        filepath: str, start_line: int = 1, end_line: int = 200
    ) -> str:
        """Read a file with line numbers, similar to cat -n."""
        return context().read_file(filepath, start_line, end_line)

    @mcp.tool()
    def edit_file(filepath: str, old_str: str, new_str: str) -> str:
        """Replace exactly one old_str occurrence in filepath with new_str."""
        return context().edit_file(filepath, old_str, new_str)

    @mcp.tool()
    def list_files(directory: str = "/testbed", pattern: str = "*.py") -> str:
        """List files in a directory matching a glob pattern."""
        return context().list_files(directory, pattern)

    @mcp.tool()
    def search_code(pattern: str, file_pattern: str = "*.py") -> str:
        """Search code using a grep-like regex pattern."""
        return context().search_code(pattern, file_pattern)

    @mcp.tool()
    def search_function_or_class_definition_in_code(name: str) -> str:
        """Find function or class definitions by name."""
        return context().search_definition(name)

    @mcp.tool()
    def find_references(name: str, filepath: str = "", line: int = 0) -> str:
        """Find references to a symbol name."""
        return context().find_references(name, filepath, line)

    @mcp.tool()
    def run_tests() -> str:
        """Execute the SWE-bench evaluation script or pytest fallback."""
        return context().run_tests()

    @mcp.tool()
    def get_patch() -> str:
        """Return git -c core.fileMode=false diff for current repository changes."""
        return context().get_patch()

    @mcp.tool()
    def run_command(command: str, workdir: str = "/testbed") -> str:
        """Run a shell command in the given workdir and return stdout, stderr, exit code."""
        return context().run_command(command, workdir)

    @mcp.resource("agent://swebench/task")
    def task_resource() -> str:
        """Expose the current task metadata as an MCP resource."""
        task_file = context().task_file
        return (
            task_file.read_text() if task_file and task_file.exists() else "{}"
        )

    @mcp.prompt()
    def swebench_debugger() -> str:
        """Provide a system prompt guideline for the agent workflow."""
        return (
            "Explore the repository with read_file/search_code, make minimal edits, "
            "run focused tests, then call final_answer(get_patch())."
        )

    return mcp


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--http",
        action="store_true",
        help="serve streamable HTTP instead of stdio",
    )
    args = parser.parse_args()
    transport = "streamable-http" if args.http else "stdio"

    # MCP v2 Migration: run() handles transport allocation internally
    build_server().run(transport=transport)


if __name__ == "__main__":
    main()
