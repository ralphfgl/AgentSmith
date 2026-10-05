"""Packaged CLI entrypoint for MBPP"""

import argparse
from pathlib import Path

import agentsmith
from agentsmith.agent import run_mbpp_agent
from agentsmith.env import load_env_file
from agentsmith.models import SandboxConfig


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--task-file", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--model-name", required=True)
    parser.add_argument("--provider-url", required=True)
    parser.add_argument("--env-file", default=None)
    parser.add_argument("--sandbox-config", default=None)
    parser.add_argument("--max-iterations", default=10)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    load_env_file(args.env_file)
    result = run_mbpp_agent(
        task_file=Path(args.task_file),
        output_file=Path(args.output),
        model_name=args.model_name,
        provider_url=args.provider_url,
        sandbox_config=args.sandbox_config,
        max_iterations=args.max_iterations,
    )
    if not result.success:
        raise SystemExit(1)
