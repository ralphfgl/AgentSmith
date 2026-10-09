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
from agentsmith.models import SWEBenchTaskInput
import textwrap

MAX_OUTPUT_CHARS = 20_000


def truncate(text: str, limit: int = MAX_OUTPUT_CHARS) -> str:
    if len(text) <= limit:
        return text
    return f"\n\n[output truncated: {len(text) - limit} characters omitted]"


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

    return textwrap.dedent(code).strip()


def _strip_noise(text: str) -> str:
    if not text:
        return text
    lines = [
        ln
        for ln in text.splitlines()
        if not ln.startswith("Emulate Docker CLI using podman.")
        and "Create /etc/containers/nodocker" not in ln
    ]
    return "\n".join(lines)


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
                nano_cpus=int(self.cpus * 1_000_000_000),
                pids_limit=self.pids_limit,
                network_disabled=True,
                cap_drop=["ALL"],
                security_opt=["no-new-privileges:true"],
                tmpfs={
                    "/tmp/": (f"rw,noexec,nosuid,nodev,size={self.tmpfs_size}")
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
        self.start()

        assert self._container is not None
        workdir = str(_normalize_testbed_path(workdir))
        process = subprocess.Popen(
            [
                "docker",
                "exec",
                "-w",
                workdir,
                str(self._container.id),
                *command,
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            stdout, stderr = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            process.kill()
            stdout, stderr = process.communicate()
            stderr += f"\nCommand timed out after {timeout} seconds"
            return 124, stdout, stderr
        return process.returncode, stdout, stderr

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
            for nb in range(start, end + 1):
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
            old = str(sys.argv[2])
            new = str(sys.argv[3])
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
                        matched = fnmatch.fnmatch(file, pattern) or fnmatch.fnmatch(relative_text, pattern)
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
            import os
            import fnmatch
            import pathlib
            import sys
            import re

            pattern = sys.argv[1]
            file_pattern = sys.argv[2]
            regex = re.compile(pattern)
            ignored = {".git", ".venv", "venv", "__pycache__", ".mypy_cache", ".pytest_cache", ".tox", ".ruff_cache"}
            count = 0
            for root, dirs, files in os.walk("/testbed"):
                dirs[:] = [d for d in dirs if d not in ignored]
                for file in files:
                    path = pathlib.Path(root) / file
                    rel = str(path.relative_to("/testbed"))
                    if not (fnmatch.fnmatch(file, file_pattern) or fnmatch.fnmatch(rel, file_pattern)):
                        continue
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

    def run_command(
        self, command: str, workdir: str = "/testbed", timeout: int = 120
    ) -> str:
        """Execute shell command."""

        workdir = str(_normalize_testbed_path(workdir))
        exit_code, stdout, stderr = self.exec(
            ["/bin/bash", "-lc", command], workdir=workdir, timeout=timeout
        )
        output_parts = []
        if stdout:
            output_parts.append("stdout:\n" + stdout)
        if stderr:
            output_parts.append("stderr:\n" + stderr)
        output_parts.append(f"exit_code: {exit_code}")
        return truncate("\n\n".join(output_parts))

    def run_tests(self, eval_script: str, timeout: int = 300) -> str:
        """Execute the evaluation script"""

        if not eval_script:
            return "run_tests failed: the SWE-bench has no eval script"
        return self.run_command(
            eval_script, workdir="/testbed", timeout=timeout
        )

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


@dataclass
class SWEBenchToolContext:
    """MCP-facing SWE-bench context."""

    task: SWEBenchTaskInput
    workspace: DockerWorkspace

    @classmethod
    def from_task(
        cls,
        task: SWEBenchTaskInput,
    ) -> "SWEBenchToolContext":
        workspace = DockerWorkspace(
            image=task.docker_image,
            memory=os.environ.get(
                "AGENT_SMITH_DOCKER_MEMORY",
                "8g",
            ),
            cpus=float(
                os.environ.get(
                    "AGENT_SMITH_DOCKER_CPUS",
                    "4",
                )
            ),
            pids_limit=int(
                os.environ.get(
                    "AGENT_SMITH_DOCKER_PIDS",
                    "512",
                )
            ),
        )

        return cls(
            task=task,
            workspace=workspace,
        )

    @classmethod
    def from_env(cls) -> "SWEBenchToolContext":
        task_file = os.environ.get("AGENT_SMITH_TASK_FILE")
        if not task_file:
            raise RuntimeError("AGENT_SMITH_TASK_FILE is not set.")

        task = SWEBenchTaskInput.model_validate_json(task_file)
        return cls.from_task(task)

    def read_file(
        self,
        filepath: str,
        start_line: int = 1,
        end_line: int = 200,
    ) -> str:
        return self.workspace.read_file(
            filepath,
            start_line,
            end_line,
        )

    def edit_file(
        self,
        filepath: str,
        old_str: str,
        new_str: str,
    ) -> str:
        return self.workspace.edit_file(
            filepath,
            old_str,
            new_str,
        )

    def list_files(
        self,
        directory: str = "/testbed",
        pattern: str = "",
    ) -> str:
        return self.workspace.list_files(
            directory,
            pattern,
        )

    def search_code(
        self,
        pattern: str,
        file_pattern: str = "*.py",
    ) -> str:
        return self.workspace.search_code(
            pattern,
            file_pattern,
        )

    def search_function_or_class_definition_in_code(
        self,
        name: str,
    ) -> str:
        return self.workspace.search_function_or_class_definition_in_code(name)

    def find_references(
        self,
        name: str,
        filepath: str,
        line: int,
    ) -> str:
        return self.workspace.find_references(
            name,
            filepath,
            line,
        )

    def run_tests(self) -> str:
        return self.workspace.run_tests(
            eval_script=self.task.eval_script,
        )

    def get_patch(self) -> str:
        return self.workspace.get_patch()

    def run_command(
        self,
        command: str,
        workdir: str = "/testbed",
    ) -> str:
        return self.workspace.run_command(
            command,
            workdir=workdir,
        )

    # def close(self) -> None:
    #     self.workspace.stop()
