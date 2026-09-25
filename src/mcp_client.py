from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
import asyncio

server_params = StdioServerParameters(
    command="python",
    args=["mpc_tools_swebench.py"],
    env={"TESTBED_PATH": "/testbed"},
)


async def run_sandbox_server():
    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()  # requires handshake
            # discover tool
            tools_response = await session.list_tools()
            for tool in tools_response.tools:
                print(f"{tool.name}: {tool.description}")
            # call a tool
            result = await session.call_tool(
                "read_file",
                {
                    "filepath": "/testbed/src/module.py",
                    "start_line": 1,
                    "end_line": 50,
                },
            )
            return result
