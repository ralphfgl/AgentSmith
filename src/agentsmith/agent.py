"""agent loop"""

import shlex
import sys
from pathlib import Path
from agentsmith.models import SolutionOutput, MBPPTaskInput


def _write_solution(path: Path, solution: SolutionOutput) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(solution.model_dump_json(indent=2))


def _create_command(script: str) -> str:
    return " ".join(
        [shlex.quote(sys.executable), shlex.quote(str(Path(script).resolve()))]
    )


def run_loop(
    *,
    benchmark: str,
    task_id: str,
    system_prompt: str,
    user_prompt: str,
    sandbox: Sandbox,
    llm: LLMClient,
    started: float,
) -> SolutionOutput:
    pass


def run_mbpp_agent(
    *,
    task_file: Path,
    output_file: Path,
    model_name: str,
    provider_url: str,
    max_retries: int = 3,
    sandbox_config: SandboxConfig | None = None,
) -> SolutionOutput:
    started = time.perf_counter()
    task = MBPPTaskInput.model_validate_json(task_file.read_text())
    env = os.environ.copy()
    env["AGENT_SMITH_TASK_FILE"] = str(task_file.resolve())
    mcp = MCPClient(
        stdio_command=_create_command("mcp_tools_mbpp.py"), env=env
    )
    try:
        mcp.start()
        sandbox = Sandbox(
            config=sandbox_config or SandboxConfig(), tool_client=mcp
        )
        system_prompt = mbpp_system_prompt(sandbox.manual())
        llm = LLMClient(
            LLMConfig(
                provider_url=provider_url,
                model_name=model_name,
                api_keys=api_keys_for_provider(provider_url, api_key_env),
                max_tokens=900,
                max_retries=max_retries,
            )
        )
        solution = run_loop(
            benchmark="mbpp",
            task_id=str(task.task_id),
            system_prompt=system_prompt,
            user_prompt=mbpp_user_prompt(task),
            sandbox=sandbox,
            llm=llm,
            max_iterations=max_iterations,
            started=started,
        )
    except Exception as exc:
        manual = "Sandbox manual unavailable because MCP startup failed."
        system_prompt = mbpp_system_prompt(manual)
        solution = _failure_solution(
            benchmark="mbpp",
            task_id=str(task.task_id),
            system_prompt=system_prompt,
            started=started,
            error=f"{type(exc).__name__}: {exc}",
        )
    finally:
        try:
            mcp.close()
        except Exception:
            pass
    _write_solution(output_file, solution)
    return solution
