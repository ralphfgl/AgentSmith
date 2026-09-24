"""Minimal language level sandbox for LLM generated code."""

import builtins  # used to copy safe builtins
import io  # StringIO buffers to capture stdout from the child
import os
import sys
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


@dataclass
class SandboxResult:
    """Result of python command executed in the sandbox."""

    stdout: str = ""
    stderr: str = ""
    error: str | None = None  # traceback string
    final_answer: str | None = None  # value passed to final_answer
    timed_out: bool = False  # if hit wall-clock limit
    truncated: bool = False  # if output was cut


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
        if not _import_allowed(name, authorized) or _import_allowed(
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


def _worker(code: str, config_dict: dict[str, Any], conn) -> None:
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
    try:
        with redirect_stdout(stdout_buf), redirect_stderr(stderr_buf):
            # passing same dict as glob and local mimic how module level code behaves in std python
            exec(compile(code, "<sandbox>", "exec"), namespace, namespace)
        # on normal completion send a success message with capturedd output
        conn.send(
            {
                "ok": True,
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
                "ok": True,
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
                "ok": True,
                "stdout": stdout_buf.getvalue(),
                "stderr": stderr_buf.getvalue(),
                "error": f"Execution hit timeout:: {e}",
                "final_answer": None,
                "timed_out": True,
            }
        )
    except (KeyboardInterrupt, SystemExit):
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


## Parent
def _truncate(text: str, limit: int) -> tuple[str, bool]:
    """truncatoin is text > limit"""

    if len(text) <= limit:
        return text, False
    return text[:limit] + f"\n <truncated {len(text) - limit} chars>", True


class Sandbox:
    def __init__(self, config: SandboxConfig | None = None):
        self.config = config or SandboxConfig()

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
        except OSError:
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

"""
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
