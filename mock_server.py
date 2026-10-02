from mcp.server.mcpserver import MCPServer

mcp = MCPServer("AgentSmith-mock")


@mcp.tool()
def print_stuff(a: str, b: int) -> str:
    return f"{a} and {b}"


@mcp.tool()
def get_system_status() -> str:
    """Return the server uptime status"""
    return "Everything OK"


if __name__ == "__main__":
    print("Launching the MCP server on http://localhost:8000")
    # sse = server-sent events. Http option was removed from the MCP SDK
    mcp.run(transport="sse", host="127.0.0.1", port=8000)
