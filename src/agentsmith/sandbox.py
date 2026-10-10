"""Minimal language level sandbox for LLM generated code."""

import argparse
import ast  # to print the value of the last expression
import codeop  # to know if an input is complete
import builtins  # used to copy safe builtins
import io  # StringIO buffers to capture stdout from the child
import time
import os
import sys
import subprocess
import json
import pathlib  # path for the FS allowlist
import signal  # SIGALRM-based timeout in the child
import traceback  # format exception
import multiprocessing as mp  # spawn child process
from contextlib import (
    redirect_stderr,
    redirect_stdout,
)  # context manager to capture output
from dataclasses import dataclass, field
from typing import Callable, Any, final
from agentsmith.mcp_client import MCPClient
from agentsmith.models import ToolSpec, ToolClient
from pydantic import BaseModel, Field


@dataclass
class SandboxResult:
    """Result of python command executed in the sandbox."""

    stdout: str = ""
    stderr: str = ""
    error: str | None = None  # traceback string
    final_answer: str | None = None  # value passed to final_answer
    timed_out: bool = False  # if hit wall-clock limit
    truncated: bool = False  # if output was cut

    @property
    def observation(self) -> str:
        parts: list[str] = []
        if self.stdout:
            parts.append(self.stdout)
        if self.stderr:
            parts.append("stderr:\n" + self.stderr)
        if self.error:
            parts.append("error:\n" + self.error)
        if self.timed_out:
            parts.append("Execution hit the configured timeout.")
        if self.truncated:
            parts.append(
                "Tool or sandbox output was truncated due to size limits."
            )
        if self.final_answer is not None:
            parts.append("final_answer captured.")
        return "\n".join(parts).strip()


DEFAULT_ALLOWED_IMPORTS = [
    "math",
    "math.*",
    "collections",
    "collections.*",
    "re",
    "json",
    "typing",
    "typing.*",
    "functools",
    "operator",
    "heapq",
    "bisect",
    "copy",
    "string",
    "random",
    "datetime",
    "datetime.*",
    "array",
    "cmath",
    "itertools",
]

DEFAULT_ALLOWED_DIRS = ["/testbed", "/tmp/agent"]


class SandboxConfig(BaseModel):
    """Sandbox configuration.

    The default policy is an allowlist: imports, direct filesystem access, memory,
    and runtime are all denied or constrained unless explicitly configured.
    """

    authorized_imports: list[str] = Field(
        default_factory=lambda: [
            "math",
            "math.*",
            "collections",
            "collections.*",
            "itertools",
            "re",
            "json",
            "typing",
            "typing.*",
            "functools",
            "operator",
            "heapq",
            "bisect",
            "copy",
            "string",
            "random",
            "datetime",
            "datetime.*",
            "array",
            "cmath",
        ]
    )
    allowed_directories: list[str] = Field(
        default_factory=lambda: ["/testbed", "/tmp/agent"]
    )
    max_execution_time_seconds: int = 600
    max_memory_mb: int = 512
    max_output_chars: int = 20_000


# custom exception used as a control-flow signal. raised by final_anser in sandbox and catched in _worker to extract the answer
class FinalAnswerSignal(Exception):
    """Control flow signal."""

    def __init__(self, answer: str):
        super().__init__(answer)
        self.answer = answer


# glob-aware matcher
# "math.*" permit math and math.something but "math" alone only permit math
def _import_allowed(name: str, authorized: list[str]) -> bool:
    """import allowlist glob-aware
    Args:
        name: name of the module or submodule
        authorized
        authorized: list of the authorized module
    """

    for pattern in authorized:
        if pattern.endswith(".*"):
            prefix = pattern[:-2]

            if name == prefix or name.startswith(prefix + "."):
                return True
        elif name == pattern:
            return True
    return False


# NOTE: stopped here cause not sure what name is


# we use a closure to make sure the agent cannot modified the authorized list
# and to avoid infinite recursion where your custom import call the original import that would couldd your custom checker again and so on
# the closure solve this by taking a snapshot of the original un-hijacked import engine before the modification happen
def _make_restriced_import(authorized: list[str]) -> Callable[..., Any]:

    real_import = builtins.__import__

    def restricted_import(
        name, globals=None, locals=None, fromlist=(), level=0
    ):
        if level != 0:
            raise ImportError("Relative imports are disabled in the sandbox")
        root = name.split(".", 1)[0]
        if not _import_allowed(name, authorized) and not _import_allowed(
            root, authorized
        ):
            raise ImportError(f"Import blocked by sandbox: {name}")
        return real_import(name, globals, locals, fromlist, level)

    return restricted_import


def _make_safe_open(allowed_dirs: list[str]) -> Callable:
    """filesystem allowlist"""

    roots = [
        pathlib.Path(d).expanduser().resolve(strict=False)
        for d in allowed_dirs
    ]

    # mirrors the builtin open signature
    def safe_open(file, mode="r", *args, **kwargs):
        try:
            path = pathlib.Path(file).expanduser().resolve(strict=False)
        except (TypeError, ValueError) as e:
            raise PermissionError(f"Invalid path: {file!r}") from e
        # allow to open : 1. the root 2. if the path is a descendent of root
        if not any(path == root or root in path.parents for root in roots):
            raise PermissionError(
                f"FS access denied by sandbox: {path} "
                f"(allowed: {[str(r) for r in roots]})"
            )
        return builtins.open(path, mode, *args, **kwargs)

    return safe_open


_SAFE_BUILTIN_NAMES = [
    # Exceptions
    "Exception",
    "BaseException",
    "ArithmeticError",
    "AssertionError",
    "AttributeError",
    "EOFError",
    "FileNotFoundError",
    "FloatingPointError",
    "ImportError",
    "IndexError",
    "KeyError",
    "KeyboardInterrupt",
    "LookupError",
    "MemoryError",
    "NameError",
    "NotImplementedError",
    "OSError",
    "OverflowError",
    "PermissionError",
    "RecursionError",
    "RuntimeError",
    "StopIteration",
    "SyntaxError",
    "SystemExit",
    "TimeoutError",
    "TypeError",
    "UnboundLocalError",
    "ValueError",
    "ZeroDivisionError",
    "Warning",
    "UserWarning",
    "DeprecationWarning",
    # Types
    "bool",
    "bytes",
    "bytearray",
    "complex",
    "dict",
    "float",
    "frozenset",
    "int",
    "list",
    "object",
    "range",
    "set",
    "slice",
    "str",
    "tuple",
    "type",
    # Constants
    "True",
    "False",
    "None",
    "NotImplemented",
    "Ellipsis",
    # Functions
    "abs",
    "all",
    "any",
    "ascii",
    "bin",
    "callable",
    "chr",
    "classmethod",
    "dir",
    "divmod",
    "enumerate",
    "filter",
    "format",
    "getattr",
    "hasattr",
    "hash",
    "hex",
    "id",
    "isinstance",
    "issubclass",
    "iter",
    "len",
    "map",
    "max",
    "min",
    "next",
    "oct",
    "ord",
    "pow",
    "print",
    "property",
    "repr",
    "reversed",
    "round",
    "setattr",
    "sorted",
    "staticmethod",
    "sum",
    "super",
    "vars",
    "zip",
    "__build_class__",
]


def _make_safe_builtins(config: SandboxConfig) -> dict[str, Any]:
    """builtin restrictions"""

    safe: dict[str, Any] = {}
    for name in _SAFE_BUILTIN_NAMES:
        # dynamically exctract the original function pointer from builtins
        # hasattr to make sure that this version of python has the builtin function
        if hasattr(builtins, name):
            safe[name] = getattr(builtins, name)
    safe["__import__"] = _make_restriced_import(config.authorized_imports)
    safe["open"] = _make_safe_open(config.allowed_directories)
    safe["input"] = lambda *a, **k: (_ for _ in ()).throw(
        RuntimeError("input() disabled")
    )
    safe["exit"] = lambda *a, **k: (_ for _ in ()).throw(
        RuntimeError("exit() disabled")
    )
    safe["quit"] = safe["exit"]
    safe["help"] = lambda *a, **k: None
    return safe


def _apply_ressource_limits(config: SandboxConfig) -> None:
    """Memory limit, applied once when the child starts."""

    try:
        import resource

        mem = max(16, config.max_memory_mb * 1024 * 1024)
        for name in ("RLIMIT_AS", "RLIMIT_DATA"):
            lim = getattr(resource, name, None)
            if lim is not None:
                try:
                    resource.setrlimit(lim, (mem, mem))
                except (OSError, ValueError):
                    pass
    except ImportError:
        pass


def _start_timer(config: SandboxConfig) -> None:
    """Timeout, re-armed for EVERY entry (the child now lives across entries)."""

    try:

        def _on_alarm(signum, frame):
            raise TimeoutError(
                f"Sandbox timeout after {config.max_execution_time_seconds}s"
            )

        signal.signal(signal.SIGALRM, _on_alarm)
        signal.alarm(max(1, int(config.max_execution_time_seconds)))
    # no SIGALRM on windows
    except (AttributeError, ValueError):
        pass


def _run(code: str, namespace: dict[str, Any], echo: bool) -> None:
    """exec the code. With echo, print the last expression like Python's REPL."""

    tree = ast.parse(code, "<sandbox>", "exec")
    last = None
    if echo and tree.body and isinstance(tree.body[-1], ast.Expr):
        last = ast.Expression(tree.body.pop().value)
    exec(compile(tree, "<sandbox>", "exec"), namespace)
    if last is not None:
        value = eval(compile(last, "<sandbox>", "eval"), namespace)
        if value is not None:
            print(repr(value))


def _worker(
    config_dict: dict[str, Any], tool_names: list[str], conn, persistent: bool
) -> None:
    """Child worker: builds the namespace ONCE, then runs entries one by one."""

    config = SandboxConfig(**config_dict)
    _apply_ressource_limits(config)
    namespace: dict[str, Any] = {
        "__builtins__": _make_safe_builtins(config),
        # so __name__ == __main__ block dont run
        "__name__": "__sandbox__",
    }

    def final_answer(answer: Any) -> None:
        raise FinalAnswerSignal(str(answer))

    namespace["final_answer"] = final_answer
    for name in tool_names:
        namespace[name] = _make_tool_proxy(name, conn)

    while True:
        try:
            request = conn.recv()  # {"code": ..., "echo": ...}
        except EOFError:  # parent is gone
            break
        stdout_buf, stderr_buf = io.StringIO(), io.StringIO()
        reply = {
            "type": "done",
            "error": None,
            "final_answer": None,
            "timed_out": False,
        }
        _start_timer(config)
        try:
            with redirect_stdout(stdout_buf), redirect_stderr(stderr_buf):
                _run(request["code"], namespace, request["echo"])
        except FinalAnswerSignal as e:
            reply["final_answer"] = e.answer
        except TimeoutError as e:
            reply["error"] = f"Execution hit timeout: {e}"
            reply["timed_out"] = True
        except BaseException:
            reply["error"] = traceback.format_exc()
        signal.alarm(0)
        reply["stdout"] = stdout_buf.getvalue()
        reply["stderr"] = stderr_buf.getvalue()
        conn.send(reply)
        if not persistent:
            break
    conn.close()


## Parent
def _truncate(text: str, limit: int) -> tuple[str, bool]:
    """truncatoin is text > limit"""

    if len(text) <= limit:
        return text, False
    return text[:limit] + f"\n <truncated {len(text) - limit} chars>", True


class Sandbox:
    def __init__(
        self,
        config: SandboxConfig | None = None,
        tool_client: ToolClient | None = None,
        persistent: bool = False,  # True: keep variables between execute() calls
    ):
        self.config = config or SandboxConfig()
        self.tool_client = tool_client
        self.persistent = persistent
        self._proc = None
        self._conn = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def close(self) -> None:
        """Kill the child. The next execute() starts a fresh one."""
        if self._proc is not None:
            if self._proc.is_alive():
                self._proc.terminate()
            self._proc.join(timeout=1)
            self._conn.close()
        self._proc = self._conn = None

    def tool_specs(self) -> list[ToolSpec]:
        if not self.tool_client:
            return []
        return self.tool_client.list_tools()

    def manual(self) -> str:
        """Generate the dynamic manual the LLM receives in the system prompt."""

        lines = [
            "Sandbox execution manual:",
            "- Write exactly one Python code block per step.",
            "- Tool calls are ordinary Python function calls.",
            "- Use final_answer(answer_string) when the task is solved.",
            "- Positional arguments are rejected for MCP tools; use keyword arguments.",
            "",
            "Available tools:",
            "- final_answer(answer: str): terminate the agent loop and return the solution.",
        ]
        for spec in self.tool_specs():
            schema = json.dumps(spec.input_schema or {}, ensure_ascii=False)
            description = (
                spec.description.strip() or "No description provided."
            )
            lines.append(f"- {spec.name}: {description} schema={schema}")
        return "\n".join(lines)

    def execute(self, code: str, echo: bool = False) -> SandboxResult:
        if not code.strip():
            return SandboxResult(error="No code provided.")
        # first compile in the parent to catch syntax error without a round trip
        try:
            compile(code, "<sandbox>", "exec")
        except SyntaxError:
            return SandboxResult(
                error="SyntaxError:\n" + traceback.format_exc(limit=0)
            )
        if self._proc is None:  # start the child (only once if persistent)
            tool_names = [tool.name for tool in self.tool_specs()]
            ctx = mp.get_context(
                "fork" if "fork" in mp.get_all_start_methods() else "spawn"
            )
            self._conn, child_conn = ctx.Pipe(duplex=True)
            self._proc = ctx.Process(
                target=_worker,
                args=(
                    self.config.model_dump(),
                    tool_names,
                    child_conn,
                    self.persistent,
                ),
            )
            self._proc.start()
            child_conn.close()
        proc, conn = self._proc, self._conn
        conn.send({"code": code, "echo": echo})
        final_message: dict[str, Any] | None = None
        # +1s: lets the child report its own timeout (with partial output) first
        deadline = (
            time.monotonic() + self.config.max_execution_time_seconds + 1
        )
        try:
            while True:
                rss_kb = _rss_kb(proc.pid) if proc.pid else None
                if (
                    rss_kb is not None
                    and rss_kb > self.config.max_memory_mb * 1024
                ):
                    self.close()
                    return SandboxResult(
                        error=(
                            "Execution exceeded the memory limit "
                            f"{rss_kb // 1024} > {self.config.max_memory_mb} MB "
                            "(sandbox state reset)"
                        )
                    )
                if conn.poll(0.05):
                    message = conn.recv()
                    if message["type"] == "tool_call":
                        conn.send(self._handle_tool_call(message))
                    elif message["type"] == "done":
                        final_message = message
                        break
                elif not proc.is_alive():
                    break
                elif time.monotonic() >= deadline:
                    self.close()
                    return SandboxResult(
                        error="Execution hit the timeout and was killed (sandbox state reset)",
                        timed_out=True,
                    )
        finally:
            if not self.persistent:
                self.close()
        if final_message is None:
            self.close()
            return SandboxResult(
                error="Sandbox process ended without a result"
            )
        stdout, t1 = _truncate(
            final_message["stdout"], self.config.max_output_chars
        )
        stderr, t2 = _truncate(
            final_message["stderr"], self.config.max_output_chars
        )
        error, t3 = _truncate(
            final_message["error"] or "", self.config.max_output_chars
        )
        return SandboxResult(
            stdout=stdout,
            stderr=stderr,
            error=error or None,
            final_answer=final_message["final_answer"],
            timed_out=final_message["timed_out"],
            truncated=t1 or t2 or t3,
        )

    def _handle_tool_call(self, message: dict[str, Any]) -> dict[str, Any]:
        if not self.tool_client:
            return {"ok": False, "error": "No MCP tool client is connected."}
        name = message.get("name", "")
        args = message.get("arguments")
        try:
            result = self.tool_client.call_tool(name, args)
            result_text = (
                result if isinstance(result, str) else json.dumps(result)
            )
            result_text, truncated = _truncate(
                result_text, self.config.max_output_chars
            )
            if truncated:
                result_text += (
                    "\nTool output was truncated due to sandbox size limits."
                )
            return {"ok": True, "result": result_text}
        except Exception as e:
            return {"ok": False, "error": f"{type(e).__name__}: {e}"}


def _make_tool_proxy(name: str, conn):
    """Factory function that takes the name of an MCP tool
    and a multiprocessing connection pipe object."""

    def proxy(*args, **kwargs):
        if args:
            raise TypeError(
                f"Tool {name} was called with positional arguments. "
                "Use keyword arguments so MCP schemas remain explicit."
            )
        conn.send({"type": "tool_call", "name": name, "arguments": kwargs})
        response = conn.recv()
        if not response.get("ok"):
            raise RuntimeError(response.get("error", f"Tool {name} failed."))
        return response.get("result", "")

    return proxy


def _rss_kb(pid: int) -> int | None:
    try:
        completed = subprocess.run(
            ["ps", "-o", "rss=", "-p", str(pid)],
            text=True,
            capture_output=True,
            timeout=1,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if completed.returncode != 0:
        return None
    text = completed.stdout.strip()
    if not text:
        return None
    try:
        # takes last line to avoid the header pritned above
        return int(text.splitlines()[-1].strip())
    except ValueError:
        return None


def _is_incomplete(source: str) -> bool:
    """True if the input needs more lines (open def/if/for block, open bracket)."""
    try:
        return codeop.compile_command(source, "<sandbox>", "single") is None
    except (SyntaxError, ValueError, OverflowError):
        return False  # real error: let the sandbox report it


def _report(result) -> None:
    if result.stdout:
        print(result.stdout, end="" if result.stdout.endswith("\n") else "\n")
    if result.stderr:
        print(result.stderr, end="", file=sys.stderr)
    if result.error:
        print(f"[error] {result.error}", file=sys.stderr)
    if result.final_answer is not None:
        print(f"[final_answer] {result.final_answer}")


def _repl(sandbox: Sandbox) -> None:
    print("Sandbox REPL. Type `exit` or Ctrl+D to quit.")
    print(
        "In a `...` block: empty line = run it, ESC then Enter (or Ctrl+C) = cancel it."
    )
    print(sandbox.manual())
    if not sys.stdin.isatty():
        source = sys.stdin.read()
        if source.strip():
            try:
                _report(sandbox.execute(source, echo=True))
            except KeyboardInterrupt:
                print("\nInterrupted (sandbox state reset)")
        return
    lines: list[str] = []
    while True:
        try:
            line = input("... " if lines else ">>> ")
        except EOFError:  # Ctrl+D
            print()
            break
        except KeyboardInterrupt:  # Ctrl+C: cancel current input
            print("\nKeyboardInterrupt")
            lines = []
            continue
        if "\x1b" in line:  # ESC (+ Enter): cancel current input
            print("(cancelled)")
            lines = []
            continue
        if not lines and line.strip() in {"exit", "quit"}:
            break
        if not lines and not line.strip():
            continue
        lines.append(line)
        source = "\n".join(lines)
        if _is_incomplete(source):
            continue
        lines = []
        try:
            result = sandbox.execute(source, echo=True)
        except KeyboardInterrupt:  # Ctrl+C while running
            print("\nInterrupted (sandbox state reset)")
            continue
        if result.stdout:
            print(
                result.stdout, end="" if result.stdout.endswith("\n") else "\n"
            )
        if result.stderr:
            print(result.stderr, end="", file=sys.stderr)
        if result.error:
            print(f"[error] {result.error}", file=sys.stderr)
        if result.final_answer is not None:
            print(f"[final_answer] {result.final_answer}")


def main() -> None:
    parser = argparse.ArgumentParser(prog="sandbox")
    parser.add_argument("config", nargs="?", help="sandbox config JSON file")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--mcp-stdio", help='e.g. "python mcp_tools_mbpp.py"')
    # group.add_argument(
    #     "--mcp-server",
    #     nargs="?",
    #     const="http://127.0.0.1:8000/sse",
    #     help="MCP server URL",
    # )
    group.add_argument(
        "--mcp-server", default=None, help="MCP streamable HTTP server URL"
    )

    args = parser.parse_args()

    config = SandboxConfig()
    if args.config:
        config = SandboxConfig.model_validate_json(
            pathlib.Path(args.config).read_text()
        )
    client = None
    if args.mcp_stdio or args.mcp_server:
        client = MCPClient(
            stdio_command=args.mcp_stdio, server_url=args.mcp_server
        )
        client.start()
    try:
        with Sandbox(config, client, persistent=True) as sandbox:
            _repl(sandbox)
    finally:
        if client:
            client.close()


if __name__ == "__main__":
    main()
