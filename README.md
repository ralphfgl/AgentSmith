https://modelcontextprotocol.io/specification/2025-06-18/server/tools -> anthropic ClientSession tool call result


┌────────────────────────────────────────────────────────────────────────┐
│ HOST PYTHON PROCESS (AgentSmith)                                       │
│                                                                        │
│   MAIN THREAD (Synchronous)           BACKGROUND WORKER (Asynchronous) │
│ ┌───────────────────────────┐       ┌────────────────────────────────┐ │
│ │ • Linear Agent Logic      │       │ • asyncio Event Loop Engine    │ │
│ │ • client.call_tool()      │ ───►  │ • Manages read/write streams   │ │
│ │ • Frozen at .result()     │ ◄───  │ • Fires JSON-RPC Packets       │ │
│ └───────────────────────────┘       └────────────────────────────────┘ │
└─────────────────────────────────────────────────────▲──────────────────┘
                                                      │
                                           OS Pipes (stdin / stdout)
                                                      │
┌─────────────────────────────────────────────────────▼──────────────────┐
│ ISOLATED OS SUBPROCESS (Local MCP Server)                              │
│                                                                        │
│   • Persistent Loop in Memory                                          │
│   • Parses inbound JSON-RPC messages                                    │
│   • Executes the raw Python tool functions locally                     │
└────────────────────────────────────────────────────────────────────────┘

Distributed Hybrid Architecture. 
The Main Thread (Synchronous): This is your main agent loop. It executes code sequentially line-by-line (e.g., Think \[\rightarrow \] Call Tool \[\rightarrow \] Parse Result). It cannot use await because it cannot yield control. When it requests a tool execution, it delegates the command to the background worker and goes to sleep via a thread-safe Future.result() block. 
The Background Client (Asynchronous): This worker runs an independent asyncio loop engine. It handles all network stream management, protocol handshakes, parsing incoming chunk frames, and tracking JSON-RPC request-response matchings. It sits patiently waiting for commands from the main thread, dispatches them down the OS pipes, and wakes the main thread back up once data arrives. 
The MCP Server (Isolated Subprocess): Launched natively via OS-level pipes (stdin/stdout), this is an entirely separate system engine. It runs continuously. It receives raw byte packets from the client thread, maps the target string to a Python function inside its own execution boundaries, calculates the result, and flashes it back across the system pipeline. 



MAIN PROCESS
  main thread: run_loop → Sandbox.execute()
      │ fork
      ▼
  SANDBOX CHILD (forked): exec(LLM code) → run_tests(candidate_code=...)
      │ multiprocessing Pipe: {"type": "tool_call", ...}
      ▼
  main thread (waiting in Sandbox.execute's poll loop) → mcp.call_tool()
      │ run_coroutine_threadsafe
      ▼
  background thread (asyncio loop, owns the MCP session)
      │ stdin/stdout JSON-RPC
      ▼
MCP SERVER PROCESS (mcp_server_mbpp.py)
  MBPPToolContext.run_tests()
      │ subprocess.run
      ▼
  TEST SUBPROCESS: candidate_test.py → exit_code / stdout / stderr


To discover free models:
On grok:
uv run --env-file .env --with groq python3 -c "
import os
from groq import Groq

client = Groq(
    api_key=os.environ.get('GROQ_API_KEY')
)

models = client.models.list()
for model in models.data:
    print(model.id)
"

to run with openrouter:
uv run python -m agent_mbpp   --env-file .env --task-file cache/mbpp_task.json   --output cache/mbpp_solution.json   --model-name "google/gemma-4-31b-it:free"  --provider-url "https://openrouter.ai/api/v1"

to run with groq:
uv run python -m agent_mbpp   --env-file .env --task-file cache/mbpp_task.json   --output cache/mbpp_solution.json   --model-name "openai/gpt-oss-120b"  --provider-url "http
s://api.groq.com/openai/v1"


to use podman at 42:
start podman user socket
systemctl --user enable --now podman.socket

set docker env var:
export DOCKER_HOST="unix:///run/user/$(id -u)/podman/podman.sock"

