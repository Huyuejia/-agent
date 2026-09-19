"""Command-line entry point for reproducible Workflow/Agent Eval runs."""

from __future__ import annotations

import argparse
import importlib
import json
from collections.abc import Sequence
from pathlib import Path
from typing import Callable

from app.evaluation.harness import EvalRunner, load_cases, summarize_results


def _load_runner(factory_path: str) -> EvalRunner:
    try:
        module_name, attribute = factory_path.split(":", 1)
        factory: Callable[[], EvalRunner] = getattr(importlib.import_module(module_name), attribute)
    except (ImportError, AttributeError, ValueError) as error:
        raise ValueError(
            "executor factory must be importable as module_path:callable_name"
        ) from error
    runner = factory()
    if not isinstance(runner, EvalRunner):
        raise ValueError("executor factory must return EvalRunner")
    return runner


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run version-controlled EvalCase JSONL")
    parser.add_argument("--cases", type=Path, required=True, help="EvalCase JSONL path")
    parser.add_argument(
        "--executor-factory",
        required=True,
        help="module_path:callable_name returning an EvalRunner",
    )
    parser.add_argument("--output", type=Path, required=True, help="result JSONL path")
    parser.add_argument(
        "--regression-cases",
        type=Path,
        help="optional independent runtime_badcase or manual_regression JSONL path",
    )
    parser.add_argument("--report", type=Path, help="optional metrics report JSON path")
    args = parser.parse_args(argv)

    runner = _load_runner(args.executor_factory)
    cases = load_cases(args.cases)
    if args.regression_cases:
        cases.extend(load_cases(args.regression_cases))
    results = runner.run_cases(cases)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        "".join(
            json.dumps(result.model_dump(mode="json"), ensure_ascii=False) + "\n"
            for result in results
        ),
        encoding="utf-8",
    )
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(
            json.dumps(
                summarize_results(cases, results).model_dump(mode="json"),
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
    return 0 if all(result.expectation_met for result in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
