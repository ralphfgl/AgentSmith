"""Minimal language level sandbox for LLM generated code."""

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
from agentsmith.models import ToolSpec, ToolClient


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


@dataclass
class SandboxConfig:
    """authorized import and timeout and memory limits"""

    authorized_imports: list[str] = field(
        default_factory=lambda: list(DEFAULT_ALLOWED_IMPORTS)
    )
    allowed_directories: list[str] = field(
        default_factory=lambda: list(DEFAULT_ALLOWED_DIRS)
    )
    max_execution_time_seconds: int = (
        30  # wall clock alarm used for both SIGALRM and the parent poll
    )
    max_memory_mb: int = 512  # RLIMIT_AS/RLIMIT_DATA cap
    max_output_chars: int = 16_000  # cap for sdtout/err


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
    """Ressource limits in child"""

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

    try:

        def _on_alarm(signum, frame):
            raise TimeoutError(
                f"Sandbox timeout after {config.max_execution_time_seconds}s"
            )

        signal.signal(signal.SIGALRM, _on_alarm)
        signal.alarm(max(1, int(config.max_execution_time_seconds)))
    # attributes catches plateform without SIGALRM (windows), value error catch non main thread issue
    except (AttributeError, ValueError):
        pass


def _worker(
    code: str, config_dict: dict[str, Any], tool_names: list[str], conn
) -> None:
    """Child worker"""

    config = SandboxConfig(**config_dict)
    _apply_ressource_limits(config)
    stdout_buf = io.StringIO()
    stderr_buf = io.StringIO()
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
    try:
        with redirect_stdout(stdout_buf), redirect_stderr(stderr_buf):
            # passing same dict as glob and local mimic how module level code behaves in std python
            exec(compile(code, "<sandbox>", "exec"), namespace, namespace)
        # on normal completion send a success message with capturedd output
        conn.send(
            {
                "type": "done",
                # getvalue to retrieve text as python string
                "stdout": stdout_buf.getvalue(),
                "stderr": stderr_buf.getvalue(),
                "error": None,
                "final_answer": None,
                "timed_out": False,
            }
        )
    except FinalAnswerSignal as e:
        conn.send(
            {
                "type": "done",
                "stdout": stdout_buf.getvalue(),
                "stderr": stderr_buf.getvalue(),
                "error": None,
                "final_answer": e.answer,
                "timed_out": False,
            }
        )
    except TimeoutError as e:
        conn.send(
            {
                "type": "done",
                "stdout": stdout_buf.getvalue(),
                "stderr": stderr_buf.getvalue(),
                "error": f"Execution hit timeout:: {e}",
                "final_answer": None,
                "timed_out": True,
            }
        )
    except (KeyboardInterrupt, SystemExit) as e:
        conn.send(
            {
                "type": "control_exception",
                "exception": type(e).__name__,
                "message": str(e),
                "stdout": stdout_buf.getvalue(),
                "stderr": stderr_buf.getvalue(),
            }
        )
    except BaseException:
        conn.send(
            {
                "type": "done",
                "stdout": stdout_buf.getvalue(),
                "stderr": stderr_buf.getvalue(),
                "error": traceback.format_exc(),
                "final_answer": None,
                "timed_out": False,
            }
        )
    finally:
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
    ):
        self.config = config or SandboxConfig()
        self.tool_client = tool_client

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

    def execute(self, code: str) -> SandboxResult:
        if not code.strip():
            return SandboxResult(error="No code provided.")
        # first compile in the parent to catch syntax error without spawning a process
        try:
            compile(code, "<sandbox>", "exec")
        except SyntaxError:
            return SandboxResult(
                error="SyntaxError:\n" + traceback.format_exc()
            )
        tool_names = [tool.name for tool in self.tool_specs()]
        ctx = mp.get_context(
            "fork" if "fork" in mp.get_all_start_methods() else "spawn"
        )
        parent_conn, child_conn = ctx.Pipe(duplex=True)
        proc = ctx.Process(
            target=_worker,
            args=(code, self.config.__dict__, tool_names, child_conn),
        )
        proc.start()
        child_conn.close()
        final_message: dict[str, Any] | None = None
        deadline = time.monotonic() + self.config.max_execution_time_seconds
        try:
            while True:
                rss_kb = _rss_kb(proc.pid) if proc.pid else None
                if (
                    rss_kb is not None
                    and rss_kb > self.config.max_memory_mb * 1024
                ):
                    proc.terminate()
                    proc.join(timeout=1)
                    return SandboxResult(
                        error=(
                            "Execution exceeded the memory limit "
                            f"{rss_kb // 1024} > {self.config.max_memory_mb} MB"
                        )
                    )
                if parent_conn.poll(0.05):
                    message = parent_conn.recv()
                    msg_type = message.get("type")
                    if msg_type == "tool_call":
                        response = self._handle_tool_call(message)
                        parent_conn.send(response)
                    elif msg_type == "done":
                        done_message = message
                        break
                    elif msg_type == "control_exception":
                        proc.join(timeout=0.2)
                        exception_name = message.get("exception", "SystemExit")
                        if exception_name == "KeyboardInterrupt":
                            raise KeyboardInterrupt(message.get("message", ""))
                        raise SystemExit(message.get("message", ""))
                proc.join(timeout=0)
                if not proc.is_alive():
                    if parent_conn.poll(0.01):
                        final_message = parent_conn.recv()
                        break
                if time.monotonic() >= deadline:
                    proc.terminate()
                    proc.join(timeout=1)
                    return SandboxResult(
                        error=(
                            "Exection hit the timeout. Partial output "
                            "is unavailable because the child process did not complete a flush"
                        ),
                        timed_out=True,
                    )
        finally:
            if proc.is_alive():
                proc.terminate()
                proc.join(timeout=1)
            parent_conn.close()
        if final_message is None:
            code_info = (
                f" exit code {proc.exitcode}"
                if proc.exitcode is not None
                else ""
            )
            return SandboxResult(
                error=f"Sandbox process ended wit a result{code_info}"
            )
        stdout, t1 = _truncate(
            final_message.get("stdout") or "", self.config.max_output_chars
        )
        stderr, t2 = _truncate(
            final_message.get("stderr") or "", self.config.max_output_chars
        )
        error, t3 = _truncate(
            final_message.get("error") or "", self.config.max_output_chars
        )

        return SandboxResult(
            stdout=stdout,
            stderr=stderr,
            error=error or None,
            final_answer=final_message.get("final_answer"),
            timed_out=bool(final_message.get("timed_out", False)),
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


def _repl(sandbox: Sandbox) -> None:
    print("Sandbox REPL. Type `exit` or Ctrl+D to quit.")
    print(sandbox.manual())
    while True:
        try:
            line = input(">>> ")
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if line.strip() in {"exit", "quit"}:
            break
        if not line.strip():
            continue
        result = sandbox.execute(line)
        if result.stdout:
            print(
                result.stdout, end="" if result.stdout.endswith("\n") else "\n"
            )
        if result.stderr:
            print(
                result.stderr,
                end="" if result.stderr.endswith("\n") else "\n",
                file=sys.stderr,
            )
        if result.error:
            print(f"[error] {result.error}", file=sys.stderr)
        if result.final_answer is not None:
            print(f"[final_answer] {result.final_answer}")


if __name__ == "__main__":
    pass
