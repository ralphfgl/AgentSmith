import os
import tempfile
import pytest
import pathlib
from agentsmith.sandbox import (
    execute_code_in_sandbox,
    SandboxConfig,
    SandboxResult,
)


def test_sandbox_success_nominal():
    """Verifies correct execution of a valid script with standard output."""
    code = """
import math
print(f"Sqrt of 16 is {math.sqrt(16)}")
final_answer("Computation done")
"""
    result = execute_code_in_sandbox(code)

    assert isinstance(result, SandboxResult)
    assert "Sqrt of 16 is 4.0" in result.stdout
    assert result.final_answer == "Computation done"
    assert result.error is None
    assert result.timed_out is False


def test_sandbox_import_blocked():
    """Verifies that importing an unauthorized module (like os) is blocked."""
    code = """
import os
print(os.getcwd())
"""
    result = execute_code_in_sandbox(code)

    assert result.final_answer is None
    assert "Import blocked by sandbox: os" in result.error


def test_sandbox_relative_import_blocked():
    """Verifies that relative imports are explicitly disabled."""
    code = """
from . import local_module
"""
    result = execute_code_in_sandbox(code)
    assert "Relative imports are disabled in the sandbox" in result.error


def test_sandbox_glob_allowed_imports():
    """Verifies that submodules of glob patterns (e.g., collections.abc) work correctly."""
    code = """
from collections import abc
print("Import match success")
"""
    result = execute_code_in_sandbox(code)
    assert "Import match success" in result.stdout
    assert result.error is None


def test_sandbox_filesystem_read_write_denied():
    """Verifies that file access outside designated directories raises a PermissionError."""
    # Attempting to read a sensitive system file out of bounds
    code = """
with open("/etc/passwd", "r") as f:
    data = f.read()
"""
    result = execute_code_in_sandbox(code)
    assert "FS access denied by sandbox" in result.error


def test_sandbox_filesystem_allowed_dir():
    """Verifies that reading and writing inside an explicitly authorized directory works."""
    with tempfile.TemporaryDirectory() as tmpdir:
        # Inject our dynamic temporary directory into a custom configuration profile
        config = SandboxConfig(allowed_directories=[tmpdir])
        test_file_path = os.path.join(tmpdir, "test.txt")

        code = f"""
with open("{test_file_path}", "w") as f:
    f.write("Hello 42 Sandbox")

with open("{test_file_path}", "r") as f:
    print(f.read())
"""
        result = execute_code_in_sandbox(code, config=config)
        assert "Hello 42 Sandbox" in result.stdout
        assert result.error is None


def test_sandbox_infinite_loop_timeout():
    """Verifies that infinite execution tracks trigger the TimeoutError threshold correctly."""
    # Enforce a tight 2-second timeout window to keep unit testing fast
    config = SandboxConfig(max_execution_time_seconds=2)

    code = """
import time
print("Loop started")
while True:
    pass
"""
    result = execute_code_in_sandbox(code, config=config)

    assert "Loop started" in result.stdout
    assert result.timed_out is True
    assert (
        "Sandbox timeout" in result.error
        or "timed out at parent layer" in result.error
    )


def test_sandbox_disabled_builtins():
    """Verifies dangerous structural builtins like input() or exit() are neutralized."""
    code_input = "input('Give data:')"
    result_input = execute_code_in_sandbox(code_input)
    assert "input() disabled" in result_error_to_string(result_input.error)

    code_exit = "exit(0)"
    result_exit = execute_code_in_sandbox(code_exit)
    assert "exit() disabled" in result_error_to_string(result_exit.error)


def test_sandbox_output_truncation():
    """Verifies massive stream data outputs are cropped to protect parent system memories."""
    config = SandboxConfig(max_output_chars=50)
    code = "print('A' * 200)"

    result = execute_code_in_sandbox(code, config=config)
    assert result.truncated is True
    assert "[Output Truncated]" in result.stdout
    assert (
        len(result.stdout) <= 100
    )  # 50 core characters + length of appending warning buffer string


def result_error_to_string(error_field) -> str:
    """Helper to guard assertions from throwing exceptions if error properties are None."""
    return error_field if error_field else ""
