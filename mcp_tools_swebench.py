"""MCP server exposing SWE-bench task tools."""

from __future__ import annotations

import argparse

from agentsmith.tools import SWEBenchToolContext


ctx: SWEBenchToolContext | None = None


def context() -> SWEBenchToolContext:
    global ctx
    if ctx is None:
        ctx = SWEBenchToolContext.from_env()
    return ctx


def build_server():
    try:
        from mcp.server.mcpserver import MCPServer

        mcp = MCPServer("agent-smith-swebench")
    except ModuleNotFoundError:
        from mcp.server.fastmcp import FastMCP

        mcp = FastMCP("agent-smith-swebench")

    @mcp.tool()
    def read_file(
        filepath: str,
        start_line: int = 1,
        end_line: int = 200,
    ) -> str:
        """Read a range of lines from a file in the SWE-bench testbed."""

        return context().read_file(
            filepath=filepath,
            start_line=start_line,
            end_line=end_line,
        )

    @mcp.tool()
    def edit_file(
        filepath: str,
        old_str: str,
        new_str: str,
    ) -> str:
        """Replace old_str with new_str in a file."""

        return context().edit_file(
            filepath=filepath,
            old_str=old_str,
            new_str=new_str,
        )

    @mcp.tool()
    def list_files(
        directory: str = "/testbed",
        pattern: str = "",
    ) -> str:
        """List files in the SWE-bench testbed."""

        return context().list_files(
            directory=directory,
            pattern=pattern,
        )

    @mcp.tool()
    def search_code(
        pattern: str,
        file_pattern: str = "*.py",
    ) -> str:
        """Search source code in the SWE-bench testbed."""

        return context().search_code(
            pattern=pattern,
            file_pattern=file_pattern,
        )

    @mcp.tool()
    def search_function_or_class_definition_in_code(
        name: str,
    ) -> str:
        """Find function or class definitions matching a name."""

        return context().search_function_or_class_definition_in_code(
            name=name,
        )

    @mcp.tool()
    def find_references(
        name: str,
        filepath: str,
        line: int,
    ) -> str:
        """Find references to a symbol near a specific location."""

        return context().find_references(
            name=name,
            filepath=filepath,
            line=line,
        )

    @mcp.tool()
    def run_tests() -> str:
        """Run the SWE-bench evaluation script for the current task."""

        return context().run_tests()

    @mcp.tool()
    def get_patch() -> str:
        """Return the current git diff for the SWE-bench task."""

        return context().get_patch()

    @mcp.tool()
    def run_command(
        command: str,
        workdir: str = "/testbed",
    ) -> str:
        """Run a shell command inside the SWE-bench testbed."""

        return context().run_command(
            command=command,
            workdir=workdir,
        )

    @mcp.resource("agent://swebench/task")
    def task_resource() -> str:
        """Return the current SWE-bench task JSON."""

        return context().describe_task()

    @mcp.prompt()
    def swebench_solver() -> str:
        return (
            "Solve the SWE-bench task by inspecting the repository, "
            "searching for relevant code, editing the files, running tests, "
            "and then calling get_patch() to return the final git patch."
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
    build_server().run(transport=transport)

    # For HTTP:
    #
    # build_server().run(
    #     transport="streamable-http",
    #     host="127.0.0.1",
    #     port=8000,
    #     streamable_http_path="/mcp",
    # )


if __name__ == "__main__":
    main()
