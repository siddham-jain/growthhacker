import argparse
from pathlib import Path
from typing import Any

from app.evaluation.cli import EvaluationRuntime, command_registry
from app.evaluation.policy import features  # noqa: F401 - registers the feature rows
from app.evaluation.policy.coverage import policy_problems
from app.evaluation.policy.matrix import render_policy_matrix
from app.evaluation.policy.registry import feature_registry
from app.evaluation.reporting.markdown import write_report

MATRIX_FILE = "feature-policy-matrix.md"


async def render(args: argparse.Namespace, runtime: EvaluationRuntime) -> int:
    out = Path(args.out) if args.out else runtime.settings.evaluation.docs_dir / MATRIX_FILE
    for path in write_report(out, render_policy_matrix(feature_registry)):
        print(path)
    return 0


async def check(args: argparse.Namespace, runtime: EvaluationRuntime) -> int:
    problems = policy_problems(feature_registry)
    for problem in problems:
        print(problem)
    if not problems:
        print(f"{len(feature_registry)} features registered; every checked field is covered")
    return 1 if problems else 0


@command_registry.register("policy")
def register(groups: Any) -> None:
    parser = groups.add_parser("policy", help="feature-policy matrix (week-1 deliverable)")
    commands = parser.add_subparsers(dest="command", required=True)
    render_parser = commands.add_parser("render", help="write the matrix markdown")
    render_parser.add_argument("--out", help="defaults to docs/evaluation/feature-policy-matrix.md")
    render_parser.set_defaults(handler=render)
    commands.add_parser("check", help="fail on uncovered fields or unexplained FLAG rows").set_defaults(
        handler=check
    )
