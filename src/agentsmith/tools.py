"""Tool implementations."""

import json
import os
import time
from pathlib import Path
from dataclasses import dataclass
import tempfile
import re
import subprocess
import sys
from pathlib import Path, PurePosixPath
import docker
from docker.errors import DockerException, NotFound
from models import SWEBenchTaskInput

MAX_OUTPUT_CHARS = 20_000


def truncate(text: str, limit: int = MAX_OUTPUT_CHARS) -> str:
    if len(text) <= limit:
        return text
    return f"\n\n[output truncated: {len(text) - limit} characters omitted]"


def definition_pattern(name: str) -> str: ...


def reference_pattern(name: str) -> str: ...


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


@dataclass
class MBPPToolContext:
    task_file: Path

    @classmethod
    def from_env(cls) -> "MBPPToolContext":
        task_file = os.environ.get("AGENT_SMITH_TASK_FILE")
        if not task_file:
            raise RuntimeError(
                "AGENT_SMITH_TASK_FILE is required for MBPP tools"
            )
        return cls(Path(task_file))

    def task(self) -> dict:
        return json.loads(self.task_file.read_text())

    def public_tests_source(self, test_list: list[str] | None) -> str:
        task = self.task()
        imports = "\n".join(task.get("test_imports") or [])
        if not test_list:
            tests = "\n".join(task.get("test_list") or [])
        else:
            tests = "\n".join(test_list)
        return "\n".join(part for part in [imports, tests] if part)

    def run_tests(
        self, code: str = "", test_list: list[str] | None = None
    ) -> str:
        if not code:
            path = Path("/tmp/agent/mbpp_solution.py")
            if path.exists():
                code = path.read_text()
        if not code.strip():
            return json.dumps(
                {
                    "success": False,
                    "output": "No candidate code was provided.",
                }
            )
        task = self.task()
        test_source = self.public_tests_source(test_list)
        full_code = code + "\n" + test_source + "\n"
        with tempfile.NamedTemporaryFile(
            "w", suffix=".py", delete=False
        ) as handle:
            handle.write(full_code)
            temp_path = handle.name
        try:
            raw = run_subprocess([sys.executable, temp_path], timeout=30)
        finally:
            try:
                Path(temp_path).unlink()
            except OSError:
                pass
        success = False
        first_line = raw.splitlines()[0] if raw else ""
        if first_line.startswith("exit_code:"):
            value = first_line.split(":", 1)[1].strip()
            success = value == "0"
        return json.dumps(
            {
                "success": success,
                "output": raw,
            }
        )


TESTBED = PurePosixPath("/testbed")


def _normalize_testbed_path(path: str) -> PurePosixPath:
    """convert path in absolute /testbed path.
    Prevents path traversal attacks.
    """

    if not path:
        raise ValueError("path cannot be None")
    raw = PurePosixPath(path)
    if raw.is_absolute():
        candidate = raw
    else:
        candidate = TESTBED / raw
    candidate = PurePosixPath(candidate)
    try:
        candidate.relative_to(TESTBED)
    except ValueError as e:
        raise PermissionError(
            f"path must be locked inside {TESTBED}: {path!r}"
        ) from e
    return candidate


def _python_code(code: str) -> str:
    """handle code indentation"""
    return "\n".join(line.rstrip() for line in code.strip("\n").splitlines())


class DockerWorkspace:
    """Own one SWEbench container."""

    def __init__(
        self,
        image: str,
        *,
        memory: str = "8g",
        cpus: float = 4.0,
        pids_limit: int = 512,
        tmpfs_size: str = "512m",
        container_id: str | None = None,
    ) -> None:
        if not image and not container_id:
            raise ValueError("A Docker image or container_id is required")
        self.image = image
        self.memory = memory
        self.cpus = cpus
        self.pids_limit = pids_limit
        self.tmpfs_size = tmpfs_size
        self.container_id = container_id
        self._docker = docker.from_env()
        self._container = None
        self._started_by_us = False

    def start(self) -> None:
        if self._container is not None:
            return
        if self.container_id:
            try:
                self._container = self._docker.containers.get(
                    self.container_id
                )
                self._container.reload()
                if self._container.status != "running":
                    self._container.start()
            except DockerException as e:
                raise RuntimeError(
                    f"Could not connect to container {self.container_id}: {e}"
                ) from e
            return
        try:
            self._container = self._docker.containers.run(
                self.image,
                command=["tail", "-f", "/dev/null"],
                detach=True,
                mem_limit=self.memory,
                nano_cpus=int(self.cpus * 1_000_000_00),
                pids_limit=self.pids_limit,
                network_disabled=True,
                cap_drop=["ALL"],
                security_opt=["no-new-privileges:true"],
                tmpfs={
                    "/tmp/": (
                        f"rw,noexec, nosuid,nodev,size={self.tmpfs_size}"
                    )
                },
                volumes={},
                remove=True,
            )
            self._started_by_us = True
        except DockerException as e:
            raise RuntimeError(
                f"Could not start Docker conatainer from image {self.image!r}: {e}"
            ) from e

    def stop(self) -> None:
        if self._container is None:
            return
        if not self._started_by_us:
            return
        try:
            self._container.remove(force=True)
        except DockerException:
            pass
        finally:
            self._container = None

    def __enter__(self) -> "DockerWorkspace":
        self.start()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.stop()

    @property
    def container(self):
        self.start()
        assert self._container is not None
        return self._container

    def exec(
        self,
        command: list[str],
        *,
        workdir: str = "/testbed",
        timeout: int = 120,
    ) -> tuple[int, str, str]:
        """Execute command inside container"""

        container = self.container
        try:
            result = container.exec_run(
                command, workdir=workdir, stdout=True, stderr=True, demux=True
            )
        except DockerException as e:
            raise RuntimeError(f"Docker exec failed: {e}") from e
        exit_code = int(result.exit_code)
        stdout_bytes: bytes
        stderr_bytes: bytes
        output = result.output
        if output is None:
            stdout_bytes = b""
            stderr_bytes = b""
        elif isinstance(output, tuple):
            stdout_bytes = output[0] or b""
            stderr_bytes = output[1] or b""
        else:
            stdout_bytes = output or b""
            stderr_bytes = b""

        stdout = stdout_bytes.decode("utf-8", errors="replace")
        stderr = stderr_bytes.decode("utf-8", errors="replace")
        return exit_code, stdout, stderr

    def _path(self, filepath: str) -> str:
        pass

    # file operations
    def read_file(
        self, filepath: str, start_line: int = 1, end_line: int = 200
    ) -> str:
        if start_line < 1:
            raise ValueError("start_line must be >= 1")
        if end_line < start_line:
            raise ValueError("endline must be > startline")
        path = _normalize_testbed_path(filepath)
        code = _python_code(
            r"""
            import pathlib
            import sys

            path = pathlib.PurePosixPath(sys.argv[1])
            start = int(sys.argv[2])
            end = int(sys.argv[3])
            file_path = pathlib.Path(path)
            if not file_path.exists():
                print(f"[ERROR] - file do not exist {path}")
                raise SystemExit(2)
            if not file_path.is_file():
                print(f"[ERROR] - not a file {path}")
                raise SystemExit(2)
            text = file_path.read_text(encoding="utf-8", errors="replace")
            lines = text.splitlines()
            start = min(start, len(lines) + 1)
            end = min(end, len(lines))
            for nb in range(start, end + 1)
                print(f"{nb}: {lines[nb - 1]}")
            """
        )
        exit_code, stdout, stderr = self.exec(
            ["python", "-c", code, str(path), str(start_line), str(end_line)]
        )
        if exit_code != 0:
            return truncate(
                stdout
                or stderr
                or f"read_file failed with exit_code={exit_code}"
            )
        return truncate(stdout)

    def edit_file(self, filepath: str, old_str: str, new_str: str) -> str:
        """replace one occurence"""

        if not old_str:
            return "edit_file failed. old_str cannot be empty"
        path = _normalize_testbed_path(filepath)
        code = _python_code(
            r"""
            import pathlib
            import sys

            path = pathlib.Path(sys.argv[1])
            old = int(sys.argv[2])
            new = int(sys.argv[3])
            if not path.exists():
                print(f"[ERROR] - file do not exist {path}")
                raise SystemExit(2)
            if not path.is_file():
                print(f"[ERROR] - not a file {path}")
                raise SystemExit(2)
            text = path.read_text(encoding="utf-8", errors="strict")
            count = text.count(old)
            if count != 1:
                print(f"edit_file failed. expected only one match, found {count}")
                raise SystemExit(3)
            updated = text.replace(old, new, 1)
            path.write_text(updated, encoding="utf-8")
            print(f"Success editing file {path}")
            """
        )
        exit_code, stdout, stderr = self.exec(
            ["python", "-c", code, str(path), str(old_str), str(new_str)]
        )
        if exit_code != 0:
            return truncate(
                stdout
                or stderr
                or f"edit_file failed with exit_code={exit_code}"
            )
        # syntax check after editing
        if path.suffix == ".py":
            check_code, check_stdout, check_stderr = self.exec(
                ["python", "-m", "py_compile", str(path)]
            )
            if check_code != 0:
                return truncate(
                    "Edit applied, but python syntax check failed.\n\n"
                    + (check_stdout + check_stderr)
                )
        return truncate(stdout)

    def list_files(
        self, directory: str = "/testbed", pattern: str = ""
    ) -> str:
        """Search and list files starting from a given dir"""

        root = _normalize_testbed_path(directory)
        # NOTE: add .tox and .ruff_cache to ignored
        code = _python_code(
            r"""
            import os
            import fnmatch
            import pathlib
            import sys

            root = pathlib.Path(sys.argv[1])
            pattern = sys.argv[2]
            if not root.exists():
            print(f"[ERROR] - directory do not exits: {root}")
            raise SystemExit(2)
            if not root.is_dir():
            print(f"[ERROR] - not a directory: {root}")
            raise SystemExit(2)
            ignored = {".git", ".venv", "venv", "__pycache__", ".mypy_cache", ".pytest_cache", ".tox", ".ruff_cache"}
            for current, dirs, files in os.walk(root):
                dirs[:] = [d for d in dirs if d not in ignored]
                for file in sorted(files):
                    path = pathlib.Path(current) / file
                    relative = path.relative_to("/testbed")
                    relative_text = str(relative)
                    if not pattern:
                        matched = True
                    else:
                        matched = fnmatch.fnmatch(file, pattern) or fnmatch(relative_text, pattern)
                    if matched:
                        print(f"/testbed/{relative_text}")
            """
        )
        exit_code, stdout, stderr = self.exec(
            ["python", "-c", code, str(root), pattern]
        )
        if exit_code != 0:
            return truncate(
                stdout
                or stderr
                or f"list_files failed with exit_code={exit_code}"
            )
        return truncate(stdout)

    def search_code(self, pattern: str, file_pattern: str = "*.py") -> str:
        """Execute regex based text search across files"""

        try:
            re.compile(pattern)
        except re.error as e:
            return f"invalid regex: {e}"
        code = _python_code(
            r"""
            import fnmatch
            import os
            import fnmatch
            import pathlib
            import sys
            import sys

            pattern = sys.argv[1]
            file_pattern = sys.argv[2]
            regex = re.compile(pattern)
            ignored = {".git", ".venv", "venv", "__pycache__", ".mypy_cache", ".pytest_cache", ".tox", ".ruff_cache"}
            count = 0
            for root, dirs, files in os.walk(root):
                dirs[:] = [d for d in dirs if d not in ignored]
                for file in files:
                    if not fnmatch.fnmatch(file, file_pattern):
                        continue
                    path = pathlib.Path(root) / file
                    try:
                        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
                    except OSError:
                        continue
                    for line_nb, line in enumerate(lines, 1):
                        if regex.search(line):
                            print(f"{path}:{line_nb} {line}")
                            count += 1
                            if count >= 500:
                                print("\n[search result limit reached]")
                                raise SystemExit(0)
            """
        )
        exit_code, stdout, stderr = self.exec(
            ["python", "-c", code, pattern, file_pattern], timeout=90
        )
        if exit_code != 0:
            return truncate(
                stdout
                or stderr
                or f"search_code failed with exit_code={exit_code}"
            )
        if not stdout:
            return "No match found"
        return truncate(stdout)

    # NOTE: Add those
    def search_function_or_class_definition_in_code(self, name: str) -> str:
        """search one python function and class definition
        using structural code analysis"""

        if not name:
            return "Name cannot be empty"
        code = _python_code(
            r"""
            import ast
            import os
            import pathlib
            import sys

            target = sys.argv[1]
            ignored = {
                ".git",
                ".venv",
                "venv",
                "__pycache__",
                ".mypy_cache",
                ".pytest_cache",
                ".tox",
                ".ruff_cache",
            }
            found = 0
            for root, dirs, files in os.walk("/testbed"):
                dirs[:] = [d for d in dirs if d not in ignored]
                for file in files:
                    if not file.endswith(".py"):
                        continue
                    path = pathlib.Path(root) / file
                    try:
                        source = path.read_text(encoding="utf-8", errors="replace")
                        tree = ast.parse(source, filename=str(path))
                    except (OSError, SyntaxError, UnicodeError):
                        continue
                    for node in ast.walk(tree):
                        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                            if node.name != target:
                                continue
                            kind = (
                                "async function"
                                if isinstance(node, ast.AsyncFunctionDef)
                                else "function"
                            )
                            print(f"{path}:{node.lineno}: {kind}: {node.name}")
                            found += 1
                        elif isinstance(node, ast.ClassDef):
                            if node.name != target:
                                continue
                            print(f"{path}:{node.lineno}: class {node.name}")
                            found += 1
            if found == 0:
                print(f"No function or class definition name {target!r} found")
            """
        )
        exit_code, stdout, stderr = self.exec(
            ["python", "-c", code, name], timeout=90
        )
        if exit_code != 0:
            return truncate(
                stdout
                or stderr
                or f"definition search failed with exit_code={exit_code}"
            )
        return truncate(stdout)

    def find_references(self, name: str, filepath: str, line: int) -> str:
        """Find all the references to a symbol, filter the original reference"""

        if not name:
            return "name cannot be empty"
        if line < 0:
            return "line must be >= 0"
        source_filepath = ""
        if filepath:
            source_filepath = str(_normalize_testbed_path(filepath))
        code = _python_code(
            r"""
            import ast
            import os
            import pathlib
            import sys
            target = sys.argv[1]
            origin_file = sys.argv[2]
            origin_line = int(sys.argv[3])
            target = sys.argv[1]
            ignored = {
            ".git",
            ".venv",
            "venv",
            "__pycache__",
            ".mypy_cache",
            ".pytest_cache",
            ".tox",
            ".ruff_cache",
            }
            found = 0
            def is_reference(node):
                if isinstance(node, ast.Name):
                    return node.id == target
                if isinstance(node, ast.Attribute):
                    return node.attr == target
                return False
            for root, dirs, files in os.walk("/testbed"):
            dirs[:] = [d for d in dirs if d not in ignored]
            for file in files:
                if not file.endswith(".py"):
                    continue
                path = pathlib.Path(root) / file
                try:
                    source = path.read_text(encoding="utf-8", errors="replace")
                    tree = ast.parse(source, filename=str(path))
                except (OSError, SyntaxError, UnicodeError):
                    continue
                for node in ast.walk(tree):
                    if not is_reference(node):
                        continue
                    node_line = getattr(node, "lineno", 0)
                    if str(path) == origin_file and node_line == origin_line:
                        continue
                    print(f"{path}:{node_line}: {target}")
                    found +=1
                    if found >= 500:
                        print("\n[reference result limit reached]")
                        raise SystemExit(0)
            if found == 0:
                print(f"No references to {target!r} found")
            """
        )
        exit_code, stdout, stderr = self.exec(
            ["python", "-c", code, name, source_filepath, str(line)],
            timeout=90,
        )
        if exit_code != 0:
            return truncate(
                stdout
                or stderr
                or f"reference search failed with exit_code={exit_code}"
            )
        return truncate(stdout)

    def run_command(self, command: str, workdir: str = "/testbed") -> str:
        """Execute shell command."""

        workdir = str(_normalize_testbed_path(workdir))
        exit_code, stdout, stderr = self.exec(
            ["/bin/bash", "-lc", command], workdir=workdir
        )
        output_parts = []
        if stdout:
            output_parts.append("stdout:\n" + stdout)
        if stderr:
            output_parts.append("stderr:\n" + stderr)
        output_parts.append(f"exit_code: {exit_code}")
        return truncate("\n\n".join(output_parts))

    def run_tests(self) -> str:
        """Execute the evaluation script"""

        # if self.eval_script:
        #     command = self.eval_script.strip()
        # return self.run_command(command, workdir="/testbed", timeout=300)

    def get_patch(self) -> str:
        """Return the git diff representing the agents changes"""

        exit_code, stdout, stderr = self.exec(
            ["git", "-c", "core.fileMode=false", "diff", "--no-ext-diff"],
            workdir="/testbed",
            timeout=60,
        )
        if exit_code != 0:
            return truncate(
                "git diff failed.\n\n"
                + stdout
                + stderr
                + f"exit_code: {exit_code}"
            )
        if not stdout.strip():
            return "No git diff"
        return truncate(stdout)
