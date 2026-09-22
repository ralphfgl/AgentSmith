"""Minimal stdlib-only sandbox for LLM-generated Python code.

Design:
- Child process (multiprocessing) so runaway code can be killed.
- Restricted __builtins__ in the child namespace.
- Import allowlist (glob-aware).
- Filesystem allowlist (realpath-resolved).
- Wall-clock timeout via SIGALRM in child + join timeout in parent.
- Memory limit via resource.setrlimit in child.
- final_answer(answer) raises a signal exception caught by the parent.

Known limitations (see README):
- object.__subclasses__() traversal can reach loaded classes.
- ctypes is not importable, but if any allowed module exposes it, this breaks.
- SIGALRM only fires in the main thread of the child; that's fine here.
- Not a security boundary against a determined adversary.
"""

from __future__ import annotations

import builtins
import io
import os
import pathlib
import signal
import traceback
import multiprocessing as mp
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import dataclass, field
from typing import Any, Callable


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------


@dataclass
class SandboxResult:
    stdout: str = ""
    stderr: str = ""
    error: str | None = None
    final_answer: str | None = None
    timed_out: bool = False
    truncated: bool = False


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

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
    authorized_imports: list[str] = field(
        default_factory=lambda: list(DEFAULT_ALLOWED_IMPORTS)
    )
    allowed_directories: list[str] = field(
        default_factory=lambda: list(DEFAULT_ALLOWED_DIRS)
    )
    max_execution_time_seconds: int = 30
    max_memory_mb: int = 512
    max_output_chars: int = 16_000


# ---------------------------------------------------------------------------
# Control-flow signal
# ---------------------------------------------------------------------------


class FinalAnswerSignal(Exception):
    def __init__(self, answer: str):
        super().__init__(answer)
        self.answer = answer


# ---------------------------------------------------------------------------
# Import allowlist
# ---------------------------------------------------------------------------


def _import_allowed(name: str, authorized: list[str]) -> bool:
    for pattern in authorized:
        if pattern.endswith(".*"):
            prefix = pattern[:-2]
            if name == prefix or name.startswith(prefix + "."):
                return True
        elif name == pattern:
            return True
    return False


def _make_restricted_import(authorized: list[str]) -> Callable[..., Any]:
    real_import = builtins.__import__

    def restricted_import(
        name, globals=None, locals=None, fromlist=(), level=0
    ):
        if level != 0:
            raise ImportError("Relative imports are disabled in the sandbox")
        # Allow submodule import if its root is allowed, e.g. collections.abc
        root = name.split(".", 1)[0]
        if not (
            _import_allowed(name, authorized)
            or _import_allowed(root, authorized)
        ):
            raise ImportError(f"Import blocked by sandbox: {name}")
        return real_import(name, globals, locals, fromlist, level)

    return restricted_import


# ---------------------------------------------------------------------------
# Filesystem allowlist
# ---------------------------------------------------------------------------


def _make_safe_open(allowed_dirs: list[str]) -> Callable[..., Any]:
    roots = [
        pathlib.Path(d).expanduser().resolve(strict=False)
        for d in allowed_dirs
    ]

    def safe_open(file, mode="r", *args, **kwargs):
        try:
            path = pathlib.Path(file).expanduser().resolve(strict=False)
        except (TypeError, ValueError) as exc:
            raise PermissionError(f"Invalid path: {file!r}") from exc
        # Path must be equal to or a descendant of an allowed root.
        if not any(path == root or root in path.parents for root in roots):
            raise PermissionError(
                f"Filesystem access denied by sandbox: {path} "
                f"(allowed: {[str(r) for r in roots]})"
            )
        return builtins.open(path, mode, *args, **kwargs)

    return safe_open


# ---------------------------------------------------------------------------
# Restricted builtins
# ---------------------------------------------------------------------------

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
    safe: dict[str, Any] = {}
    for name in _SAFE_BUILTIN_NAMES:
        if hasattr(builtins, name):
            safe[name] = getattr(builtins, name)

    safe["__import__"] = _make_restricted_import(config.authorized_imports)
    safe["open"] = _make_safe_open(config.allowed_directories)
    safe["input"] = lambda *a, **k: (_ for _ in ()).throw(
        RuntimeError("input() disabled")
    )
    safe["exit"] = lambda *a, **k: (_ for _ in ()).throw(
        SystemExit("exit() disabled")
    )
    safe["quit"] = safe["exit"]
    safe["help"] = lambda *a, **k: None

    return safe


# ---------------------------------------------------------------------------
# Resource limits in child
# ---------------------------------------------------------------------------


def _apply_resource_limits(config: SandboxConfig) -> None:
    try:
        import resource

        mem = max(16, config.max_memory_mb) * 1024 * 1024
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
    except (AttributeError, ValueError):
        pass


# ---------------------------------------------------------------------------
# Child worker
# ---------------------------------------------------------------------------


def _worker(code: str, config_dict: dict[str, Any], conn) -> None:
    config = SandboxConfig(**config_dict)
    _apply_resource_limits(config)

    stdout_buf = io.StringIO()
    stderr_buf = io.StringIO()

    namespace: dict[str, Any] = {
        "__builtins__": _make_safe_builtins(config),
        "__name__": "__sandbox__",
    }

    def final_answer(answer: Any) -> None:
        raise FinalAnswerSignal(str(answer))

    namespace["final_answer"] = final_answer

    try:
        with redirect_stdout(stdout_buf), redirect_stderr(stderr_buf):
            exec(compile(code, "<sandbox>", "exec"), namespace, namespace)
        conn.send(
            {
                "ok": True,
                "stdout": stdout_buf.getvalue(),
                "stderr": stderr_buf.getvalue(),
                "error": None,
                "final_answer": None,
                "timed_out": False,
            }
        )
    except FinalAnswerSignal as exc:
        conn.send(
            {
                "ok": True,
                "stdout": stdout_buf.getvalue(),
                "stderr": stderr_buf.getvalue(),
                "error": None,
                "final_answer": exc.answer,
                "timed_out": False,
            }
        )
    except TimeoutError as exc:
        conn.send(
            {
                "ok": True,
                "stdout": stdout_buf.getvalue(),
                "stderr": stderr_buf.getvalue(),
                "error": f"Execution hit the timeout: {exc}",
                "final_answer": None,
                "timed_out": True,
            }
        )
    except (KeyboardInterrupt, SystemExit):
        # Re-raise so the parent can distinguish user-initiated exits.
        raise
    except BaseException:
        conn.send(
            {
                "ok": True,
                "stdout": stdout_buf.getvalue(),
                "stderr": stderr_buf.getvalue(),
                "error": traceback.format_exc(),
                "final_answer": None,
                "timed_out": False,
            }
        )
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Parent
# ---------------------------------------------------------------------------


def _truncate(text: str, limit: int) -> tuple[str, bool]:
    if len(text) <= limit:
        return text, False
    return text[:limit] + f"\n... <truncated {len(text) - limit} chars>", True


class Sandbox:
    def __init__(self, config: SandboxConfig | None = None):
        self.config = config or SandboxConfig()

    def execute(self, code: str) -> SandboxResult:
        if not code.strip():
            return SandboxResult(error="No code provided.")

        # Fail fast on syntax errors (still in the parent, no process spawn).
        try:
            compile(code, "<sandbox>", "exec")
        except SyntaxError:
            return SandboxResult(
                error="SyntaxError:\n" + traceback.format_exc()
            )

        ctx = mp.get_context(
            "fork" if "fork" in mp.get_all_start_methods() else "spawn"
        )
        parent_conn, child_conn = ctx.Pipe(duplex=True)
        proc = ctx.Process(
            target=_worker, args=(code, self.config.__dict__, child_conn)
        )
        proc.start()
        child_conn.close()

        message: dict[str, Any] | None = None
        try:
            if parent_conn.poll(self.config.max_execution_time_seconds + 1):
                message = parent_conn.recv()
        except EOFError:
            message = None
        finally:
            if proc.is_alive():
                proc.terminate()
                proc.join(timeout=1)
                if proc.is_alive():
                    proc.kill()
                    proc.join(timeout=1)
            parent_conn.close()

        if message is None:
            return SandboxResult(
                error=f"Sandbox produced no result (exit code {proc.exitcode}).",
                timed_out=proc.exitcode is not None and proc.exitcode < 0,
            )

        stdout, t1 = _truncate(
            message.get("stdout") or "", self.config.max_output_chars
        )
        stderr, t2 = _truncate(
            message.get("stderr") or "", self.config.max_output_chars
        )
        error, t3 = _truncate(
            message.get("error") or "", self.config.max_output_chars
        )

        return SandboxResult(
            stdout=stdout,
            stderr=stderr,
            error=error or None,
            final_answer=message.get("final_answer"),
            timed_out=bool(message.get("timed_out", False)),
            truncated=t1 or t2 or t3,
        )


# ---------------------------------------------------------------------------
# Manual REPL entry point
# ---------------------------------------------------------------------------


def _repl(config: SandboxConfig | None = None) -> None:
    sb = Sandbox(config)
    print("Sandbox REPL. Type `exit` or Ctrl+D to quit.")
    while True:
        try:
            line = input(">>> ")
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if line.strip() in {"exit", "quit"}:
            break
        result = sb.execute(line)
        if result.stdout:
            print(
                result.stdout, end="" if result.stdout.endswith("\n") else "\n"
            )
        if result.stderr:
            print(
                result.stderr,
                end="" if result.stderr.endswith("\n") else "\n",
                file=os.sys.stderr,
            )
        if result.error:
            print(f"[error] {result.error}", file=os.sys.stderr)
        if result.final_answer is not None:
            print(f"[final_answer] {result.final_answer}")


if __name__ == "__main__":
    _repl()
