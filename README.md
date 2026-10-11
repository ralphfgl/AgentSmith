*This activity has been created as part of the 42 curriculum by rfeghali.*

## Description

AgentSmith is a code agent that solves coding tasks by thinking,
writing, and running code in a loop. It is built for two benchmarks:
MBPP (small Python function problems) and SWE-bench (real bug fixes in
open-source repositories).

The goal is simple: give the agent a task, and it should explore,
implement, test, and submit a solution on its own. No fine-tuning, no
native tool-calling --- the agent only talks to its environment by
writing Python code blocks that are executed in a sandbox.

This project was an opportunity to build a full agent harness from
scratch: a reasoning loop, a sandbox, an MCP-based tool layer, an LLM
client with provider rotation, and a benchmark runner.

## Instructions

### Setup

``` bash
git clone <repo>
cd AgentSmith
uv sync
cp .env.example .env  # then add your API keys
```

You also need to add the moulinette repo and use uv sync on it.

### Run an MBPP task

``` bash
uv run python -m agent_mbpp \
  --env-file .env \
  --task-file cache/mbpp_task.json \
  --output cache/mbpp_solution.json \
  --model-name "openai/gpt-oss-120b" \
  --provider-url "https://api.groq.com/openai/v1"
```

### Run a SWE-bench task

``` bash
uv run python -m agent_swebench \
  --env-file .env \
  --task-file cache/swebench_task.json \
  --output cache/swebench_solution.json \
  --model-name "qwen/qwen3.8-27b" \
  --provider-url "https://api.groq.com/openai/v1" \
```

### Podman on the 42 cluster

SWE-bench tasks run inside Docker containers, but on 42 you'll need
Podman:

``` bash
# start the user socket
systemctl --user enable --now podman.socket

# point docker tooling at podman
export DOCKER_HOST="unix:///run/user/$(id -u)/podman/podman.sock"

# to use rootless container privileges
podman unshare
```

### Debug with the sandbox REPL

The sandbox can be launched standalone to test tool calls interactively.

Launch the MCP server over HTTP:

``` bash
AGENT_SMITH_TASK_FILE=cache/mbpp_task.json uv run python mcp_tools_mbpp.py --http
Then connect the sandbox with the HTTP client:
```

``` bash
uv run sandbox --mcp-server http://127.0.0.1:8000/mcp
```

## System architecture

The system is split across three layers, each in its own process or
thread boundary:

``` text
┌────────────────────────────────────────────────────────────────────────┐
│ HOST PROCESS (AgentSmith)                                              │
│                                                                        │
│   MAIN THREAD (sync)                  BACKGROUND THREAD (async)        │
│ ┌───────────────────────────┐       ┌────────────────────────────────┐ │
│ │ • Agent loop              │       │ • asyncio event loop           │ │
│ │ • client.call_tool()      │ ───►  │ • Manages MCP streams          │ │
│ │ • Blocks on .result()     │ ◄───  │ • Fires JSON-RPC packets       │ │
│ └───────────────────────────┘       └────────────────────────────────┘ │
└─────────────────────────────────────────────────────▲──────────────────┘
                                                      │ OS pipes (stdio)
                                                      │ or HTTP
┌─────────────────────────────────────────────────────▼──────────────────┐
│ MCP SERVER PROCESS (mcp_tools_mbpp.py / mcp_tools_swebench.py)         │
│                                                                        │
│   • Parses JSON-RPC messages                                           │
│   • Executes tool functions (read_file, edit_file, run_tests, ...)     │
│   • For SWE-bench: shells out to Docker/Podman                         │
└────────────────────────────────────────────────────────────────────────┘
The key idea: the main thread stays synchronous and simple (read code, call a tool, wait for result). A background thread owns the asyncio loop and speaks MCP. The MCP server is a separate process that actually does the work — reading files, running tests, executing commands in a container.
```

This separation matters because:

LLM-generated code runs in a forked child, not in the agent process

Tools can't be accessed by the LLM directly --- only through the sandbox
proxy

The MCP server can be swapped for a remote one over HTTP without
touching the agent

## Agent loop explanation

The loop lives in run_loop in src/agentsmith/agent.py. Each iteration is
one Thought → Code → Observation cycle:

Trim history --- keep the system prompt, initial user message, and the
last N exchanges. Token usage grows quadratically otherwise.

Call the LLM --- send the current message list to the configured
provider.

Extract code --- pull a Python block out of the response. Malformed
fences, JSON tool calls, XML `<invoke>`{=html} blocks, and ReAct Action:
formats are all normalized to Python. If nothing valid is found, the
agent gets a warning instead of crashing.

Execute in the sandbox --- the code runs in a forked child with
restricted builtins, no arbitrary imports, and a filesystem allowlist.

Observe --- stdout, stderr, and exceptions are fed back as the next user
message.

Stop or continue --- if the code called final_answer(...), the loop ends
and the answer is returned.

The final_answer function is a control-flow signal: it raises a
FinalAnswerSignal in the sandbox child, which the worker catches and
sends back through the pipe. The parent never executes LLM code
directly.

For SWE-bench there is also a rescue path: if the agent hits the
iteration limit without calling final_answer, the harness calls
get_patch() on the MCP server one last time. If there's a real diff,
it's submitted anyway.

## Sandbox design

The sandbox is the trust boundary. It uses multiprocessing with a pipe,
so the LLM's code never runs in the parent process.

### What's restricted:

Imports --- an allowlist (math, re, json, itertools, collections,
typing, etc.). A custom **import** checks every import, including
submodules, with glob support (math.\*). Relative imports are blocked.

Filesystem --- open() is wrapped to only allow paths inside /testbed and
/tmp/agent. Path traversal is caught by resolving to absolute paths and
checking ancestry.

Builtins --- a curated subset. eval, exec, compile, globals, locals,
input, and exit are removed. Exceptions and basic types are kept so
normal Python still works.

Resources --- RLIMIT_AS and RLIMIT_DATA cap memory. A SIGALRM timer caps
wall-clock time per step.

Memory monitoring --- the parent polls the child's RSS every 50ms. If it
exceeds the limit, the sandbox is killed and reset.

### How tools are exposed:

Tools aren't injected as functions the LLM can call directly. Instead,
each tool name becomes a proxy function in the sandbox namespace. When
called, the proxy sends a {"type": "tool_call", ...} message over the
pipe, and the parent forwards it to the MCP server. Positional arguments
are rejected --- everything must be keyword-only, so the MCP schema
stays explicit.

This means the LLM can write print(read_file(filepath="foo.py")) as
ordinary Python. It has no idea there's an MCP server on the other side.


### MBPP tools (MBPPToolContext):

run_tests(code, test_list) --- writes candidate code plus tests to a
temp file, runs it in a subprocess, and returns {success, output}.

### SWE-bench tools (SWEBenchToolContext):

read_file(filepath, start_line, end_line) --- line-numbered read, capped
at 200 lines.

edit_file(filepath, old_str, new_str) --- exact replacement. Fails if
old_str matches zero or more than one location, then runs py_compile on
the edited file.

list_files(directory, pattern) --- walks the repo, skips .git,
**pycache**, .venv, etc.

search_code(pattern, file_pattern) --- regex search, capped at 500 hits.

search_function_or_class_definition_in_code(name) --- AST-based search
for def and class.

find_references(name, filepath, line) --- AST-based reference search,
excluding the definition site.

run_command(command, workdir) --- shell execution inside the container.

run_tests() --- runs the task's eval script inside the container.

get_patch() --- returns git diff --no-ext-diff from /testbed.

Two workspace backends exist: DockerWorkspace (spawns a hardened
container with network_disabled, cap_drop=\["ALL"\], no-new-privileges,
memory/PID limits, and a tmpfs at /tmp) and LocalWorkspace (runs the
same tool scripts locally, for sandbox exams).

## Benchmark results and analysis

Five models were tested across three tasks: scikit-learn-13439,
django-11066, and sympy-18189.

**Pass rate: Qwen 3/3, Gemma 3/3, Nemotron 2/3, North Mini 0/3, GPT-OSS
0/3.**

See BENCHMARK_REPORT.md for more details


## Resources

### MCP and tool protocol

-   MCP specification --- Server Tools --- used to understand how
    ClientSession returns tool call.

-   MCP Python SDK --- Your First Client --- reference for the stdio
    client pattern used in MCPClient.


### Docker / Podman

-   Podman rootless docs --- used to understand podman unshare and the
    user socket setup.


## How AI was used

Debugging provider quirks --- AI helped diagnose why GPT-OSS 120B kept
failing with HTTP 400 (tool_choice=none but the model still emitted a
tool call).

Writing this README
Writing the benchmark analysis --- the comparison table.
