FROM python:3.11-slim

# Install minimal tools needed for SWE-bench (like git for patches)
RUN apt-get update && apt-get install -y git && rm -rf /var/lib/apt/lists/*

# Install uv using the official installer script
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# Create a non-privileged system user for security
RUN useradd -m -u 1000 sandbox

# Setup the workspace directories
WORKDIR /workspace
RUN mkdir /testbed && chown -R sandbox:sandbox /testbed /workspace

# Install the MCP server's actual dependency (the "agent-smith" PyPI package
# is an unrelated project and does not provide src.tools.HostBackend)
RUN uv pip install --system --no-cache mcp

# Copy your server script AND the local src/ package it imports from
COPY mcp_tools_swebench.py /workspace/mcp_tools_swebench.py
COPY src/ /workspace/src/
RUN chown -R sandbox:sandbox /workspace

# Switch to the non-root user to enforce sandbox security constraints
USER sandbox

# Must match the env var name read in mcp_tools_swebench.py's context()
ENV AGENT_SMITH_TESTBED_PATH=/testbed

# Run the server via stdio transport
CMD ["python", "mcp_tools_swebench.py"]
