"""Evaluate deterministic retrieval routing against the checked-in golden set."""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.retrieval.query_analyzer import QueryAnalyzer  # noqa: E402


def main() -> int:
    path = ROOT / "evaluation" / "retrieval_routing_golden.jsonl"
    cases = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
    analyzer = QueryAnalyzer()
    route_hits = 0
    entity_hits = 0
    failures = []
    mode_counts: Counter[str] = Counter()
    for case in cases:
        plan = analyzer.analyze(case["query"])
        modes = [step.mode.value for step in plan.steps]
        entities = [f"{item.type}:{item.normalized_value}" for item in plan.detected_entities]
        mode_counts.update(modes)
        route_ok = modes == case["expected_modes"]
        entity_ok = entities == case["expected_entities"]
        route_hits += int(route_ok)
        entity_hits += int(entity_ok)
        if not route_ok or not entity_ok:
            failures.append({"case_id": case["case_id"], "modes": modes, "entities": entities})
    result = {
        "cases": len(cases),
        "routing_accuracy": route_hits / len(cases),
        "entity_accuracy": entity_hits / len(cases),
        "planned_steps": dict(sorted(mode_counts.items())),
        "failures": failures,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
