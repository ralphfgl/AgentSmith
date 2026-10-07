"""Models."""

from typing import List, Optional, Any
from pydantic import BaseModel, Field
from datetime import datetime
from typing import Protocol


class StepMetrics(BaseModel):
    """Metrics for a single agent step.
    Each step corresponds to one Though Code Observation cycle."""

    step: int = Field(..., description="iteration number")
    input_tokens: int = Field(..., description="tokens sent to the LLM")
    output_tokens: int = Field(..., description="tokens generated")
    request_time_ms: float = Field(..., description="LLM API call in ms")
    timestamp: str = Field(
        default_factory=lambda: datetime.now().isoformat(),
        description="ISO timestamp of this step",
    )
    api_url: str = Field(default="", description="URL of LLM API endpoint")
    model_name: str = Field(default="", description="model identifier")
    llm_output: str = Field(default="", description="raw text gen by LLM")
    sandbox_input: str = Field(default="")
    sandbox_output: str = Field(default="", description="stdout/stderr/error")
    retries: int = Field(default=0, description="nb of API retries")


class SolutionOutput(BaseModel):
    """Output from student solution, required format for evaluation.
    This is the JSON structure your agent must produce and write to solution.json.
    The moulinette validates this against task correctness and metrics limits.
    """

    task_id: str = Field(
        ...,
        description="Task identifier (MBPP task_id as string, or SWE-bench instance_id)",
    )
    benchmark: str = Field(
        ..., description="Benchmark type: 'mbpp' or 'swebench'"
    )
    success: bool = Field(
        ..., description="Whether the agent believes it solved the task"
    )
    solution: str = Field(
        ...,
        description="For MBPP: the Python function code. For SWE-bench: the git patch (diff)",
    )
    iterations: int = Field(
        ..., description="Number of agent loop iterations used"
    )
    total_requests: int = Field(
        ...,
        description="Total number of LLM API requests made (including retries)",
    )
    total_input_tokens: int = Field(
        ..., description="Sum of input_tokens across all steps"
    )
    total_output_tokens: int = Field(
        ..., description="Sum of output_tokens across all steps"
    )
    total_time_seconds: float = Field(
        ..., description="Wall-clock time from agent start to finish"
    )
    steps: List[StepMetrics] = Field(
        default_factory=list,
        description="Per-step metrics, one entry per agent iteration",
    )
    system_prompt: str = Field(
        default="",
        description="Full system prompt sent to the LLM (for provenance checking)",
    )
    error: Optional[str] = Field(
        default=None,
        description="Error message if the agent failed (None if successful)",
    )
    timestamp: str = Field(
        default_factory=lambda: datetime.now().isoformat(),
        description="ISO 8601 timestamp of when the solution was produced",
    )


class SandboxConfig(BaseModel):
    """Sandbox configuration for student solutions.
    Uses allowlist approach: only imports in authorized_imports are allowed.
    Everything else is blocked by default.
    """

    authorized_imports: List[str] = Field(
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
    allowed_directories: List[str] = Field(
        default_factory=lambda: ["/testbed", "/tmp/agent"]
    )
    max_execution_time_seconds: int = 30
    max_memory_mb: int = 512


class SWEBenchTaskInput(BaseModel):
    """Input for a SWE-bench task."""

    instance_id: str = Field(..., description="SWE-bench instance identifier")
    problem_statement: str = Field(..., description="GitHub issue description")
    docker_image: str = Field(..., description="Docker image")
    eval_script: str = Field(..., description="script to evaluate the patch")
    hints_text: str = Field(default="", description="optional hints")
    repo: str = Field(default="", description="repository name")


class MBPPTaskInput(BaseModel):
    """Input for MBPP task evaluation."""

    task_id: int
    task_definition: str
    function_definition: str
    test_imports: List[str] = Field(default_factory=list)
    test_list: List[str] = Field(default_factory=list)


class LLMResponse(BaseModel):
    """Normalized LLM API response."""

    text: str
    input_tokens: int
    output_tokens: int
    latency_ms: float
    retries: int = 0


class ToolSpec(BaseModel):
    """Serializable view of an MCP tool schema"""

    name: str
    description: str = ""
    input_schema: dict[str, Any] = Field(default_factory=dict)


class ToolClient(Protocol):
    """Tool client interface used by sandbox parent."""

    def list_tools(self) -> list[ToolSpec]: ...
    def call_tool(self, name: str, arguments: dict[str, Any]) -> str: ...
