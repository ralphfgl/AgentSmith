"""Tool implementations."""

import fnmatch
import re
import subprocess
import sys
from pathlib import Path


def truncate(text: str, limit: int = 20_000) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n... <truncated {len(text) - limit} chars>"


def run_subprocess(
    command: list[str], timeout: int = 120, cwd: str | None = None
) -> str:
    try:
        completed = subprocess.run(
            command,
            cwd=cwd,
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
        return truncate(
            f"exit_code: {completed.returncode}\n"
            f"stdout:\n{completed.stdout}\n"
            f"stderr:\n{completed.stderr}"
        )
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout if isinstance(exc.stdout, str) else ""
        stderr = exc.stderr if isinstance(exc.stderr, str) else ""
        return truncate(
            f"exit_code: timeout\nstdout:\n{stdout}\nstderr:\n{stderr}\n"
            f"Command timed out after {timeout}s"
        )


class HostBackend:
    def __init__(self, root: Path):
        self.root = root.resolve(strict=False)

    def _resolve(self, path: str) -> Path:
        candidate = Path(path)
        testbed = Path("/testbed")
        if candidate.is_absolute() and (
            candidate == testbed or testbed in candidate.parents
        ):
            candidate = self.root / candidate.relative_to("/testbed")
        elif not candidate.is_absolute():
            candidate = self.root / candidate
        resolved = candidate.resolve(strict=False)
        if resolved != self.root and self.root not in resolved.parents:
            raise PermissionError(f"Path outside testbed: {resolved}")
        return resolved

    def read_file(
        self, filepath: str, start_line: int = 1, end_line: int = 200
    ) -> str:
        path = self._resolve(filepath)
        lines = path.read_text(errors="replace").splitlines()
        start = max(1, int(start_line))
        end = min(len(lines), int(end_line))
        return "\n".join(
            f"{line_no}: {lines[line_no - 1]}"
            for line_no in range(start, end + 1)
        )

    def edit_file(self, filepath: str, old_str: str, new_str: str) -> str:
        path = self._resolve(filepath)
        content = path.read_text()
        count = content.count(old_str)
        if count != 1:
            return f"edit_file refused: expected exactly one match, found {count}."
        path.write_text(content.replace(old_str, new_str, 1))
        compile_check = run_subprocess(
            [sys.executable, "-m", "py_compile", str(path)], timeout=30
        )
        if "exit_code: 0" not in compile_check and path.suffix == ".py":
            return "Edit applied, but syntax check failed:\n" + compile_check
        return "Edit applied successfully."

    def list_files(self, directory: str, pattern: str = "*") -> str:
        root = self._resolve(directory)
        matches = sorted(
            str(path)
            for path in root.rglob("*")
            if path.is_file() and fnmatch.fnmatch(path.name, pattern)
        )
        return truncate("\n".join(matches))

    def search_code(self, pattern: str, file_pattern: str = "*.py") -> str:
        regex = re.compile(pattern)
        rows: list[str] = []
        for path in self.root.rglob("*"):
            if not path.is_file() or not fnmatch.fnmatch(
                path.name, file_pattern
            ):
                continue
            try:
                lines = path.read_text(errors="replace").splitlines()
            except OSError:
                continue
            for line_no, line in enumerate(lines, 1):
                if regex.search(line):
                    rows.append(f"{path}:{line_no} {line}")
        return truncate("\n".join(rows))

    def search_definition(self, name: str) -> str:
        """Finds explicit Python function or class definitions by name."""
        pattern = definition_pattern(name)
        return self.search_code(pattern, file_pattern="*.py")

    def find_references(
        self, name: str, filepath: str = "", line: int = 0
    ) -> str:
        """Finds all occurrences/usages of a symbol name in the Python codebase."""
        # Note: filepath and line are accepted to maintain signature compatibility
        # with the MCP tool definition but we fall back on a global repository text scan.
        pattern = reference_pattern(name)
        return self.search_code(pattern, file_pattern="*.py")

    def run_tests(self, eval_script: str = "", timeout: int = 300) -> str:
        """Executes the specific SWE-bench evaluation script or defaults to pytest."""
        if eval_script:
            # If the specific benchmark task provided an evaluation bash script, run it.
            return self.run_command(
                eval_script, workdir="/testbed", timeout=timeout
            )
        # Default fallback framework if no specific evaluation script metadata exists
        return self.run_command("pytest", workdir="/testbed", timeout=timeout)

    def run_command(
        self, command: str, workdir: str = "/testbed", timeout: int = 120
    ) -> str:
        cwd = self._resolve(workdir)
        return run_subprocess(
            ["/bin/bash", "-lc", command], cwd=str(cwd), timeout=timeout
        )

    def get_patch(self) -> str:
        completed = subprocess.run(
            ["git", "-c", "core.fileMode=false", "diff"],
            cwd=str(self.root),
            text=True,
            capture_output=True,
            timeout=60,
            check=False,
        )
        if completed.returncode == 0:
            return truncate(completed.stdout)
        return truncate(
            f"exit_code: {completed.returncode}\n"
            f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}"
        )


def definition_pattern(name: str) -> str:
    escaped = re.escape(name)
    return rf"^\s*(def|class)\s+{escaped}\b"


def reference_pattern(name: str) -> str:
    escaped = re.escape(name)
    return rf"\b{escaped}\b"
