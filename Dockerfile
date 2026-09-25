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

# Install the MCP server dependencies using uv (fast and cache-less)
RUN uv pip install --system --no-cache mcp agent-smith

# Copy your server script into the container
COPY mpc_tools_swebench.py /workspace/mpc_tools_swebench.py
RUN chown sandbox:sandbox /workspace/mpc_tools_swebench.py

# Switch to the non-root user to enforce sandbox security constraints
USER sandbox

ENV TESTBED_PATH=/testbed

# Run the server via stdio transport
CMD ["python", "mpc_tools_swebench.py"]
