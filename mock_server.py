from mcp.server.mcpserver import MCPServer

mcp = MCPServer("AgentSmith-mock")


@mcp.tool()
def calculate_backups(frequency: str, count: int) -> str:
    """
    calculate backup stat for env file.
    args:
        freq: how often it runs
        count: number of total file
    """
    return f"Processed a payload manifest for {count} files running  on a {frequency} schedule"


@mcp.tool()
def get_system_status() -> str:
    """Return the server uptime status"""
    return "Everything OK"


if __name__ == "__main__":
    print("Launching the MCP server on http://localhost:8000")
    # sse = server-sent events. Http option was removed from the MCP SDK
    mcp.run(transport="sse", host="127.0.0.1", port=8000)
