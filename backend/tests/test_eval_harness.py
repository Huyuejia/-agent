import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.evaluation.harness import (
    EvalCase,
    EvalFixture,
    EvalRunner,
    ExecutionResult,
    load_cases,
    summarize_results,
)
from app.evaluation.production_runner import build_production_runner


@pytest.mark.parametrize(
    "forbidden_key",
    [
        "answer",
        "execution_mode",
        "verification_executed",
        "failure_category",
        "supported",
    ],
)
def test_eval_fixture_rejects_execution_outcomes(forbidden_key):
    with pytest.raises(ValidationError):
        EvalFixture.model_validate({forbidden_key: "fixture-owned"})


class RecordingExecutor:
    def __init__(self, execution_mode):
        self.execution_mode = execution_mode
        self.requests = []

    def run(self, request):
        self.requests.append(request)
        return ExecutionResult(
            execution_mode=self.execution_mode,
            answer="Cam-A1 的保修期为 1 年",
            status="SUCCEEDED",
            task_id=f"{self.execution_mode}-task",
            trace_ref=f"trace:{self.execution_mode}",
            verification_executed=True,
            supported=True,
        )


def test_runner_loads_versioned_case_and_emits_one_result_per_mode(tmp_path):
    cases_path = tmp_path / "cases.jsonl"
    cases_path.write_text(
        json.dumps(
            {
                "case_id": "warranty-001",
                "version": "v1",
                "slice": "warranty",
                "complex_task": False,
                "initial_message": "查询 Cam-A1 的保修",
                "expected_boundary": "workflow",
                "required_capabilities": [],
                "forbidden_capabilities": [],
                "required_facts": ["Cam-A1", "1 年"],
                "expected_status": "SUCCEEDED",
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    case = load_cases(cases_path)[0]
    workflow = RecordingExecutor("workflow")
    agent = RecordingExecutor("agent")
    auto = RecordingExecutor("workflow")

    results = EvalRunner(
        workflow_executor=workflow,
        agent_executor=agent,
        auto_executor=auto,
    ).run_case(case)

    assert case == EvalCase.model_validate_json(cases_path.read_text(encoding="utf-8"))
    assert [result.requested_mode for result in results] == ["workflow", "agent", "auto"]
    assert all(result.success for result in results)
    assert results[-1].grader_details["boundary"].passed is True
    assert all(result.events[-1].event_type == "EVAL_RESULT" for result in results)
    assert [request.fixture for request in workflow.requests + agent.requests + auto.requests] == [
        EvalFixture(), EvalFixture(), EvalFixture()
    ]


class ClarifyingExecutor:
    def __init__(self, execution_mode):
        self.execution_mode = execution_mode
        self.requests = []

    def run(self, request):
        self.requests.append(request)
        if request.task_id is None:
            return ExecutionResult(
                execution_mode=self.execution_mode,
                status="WAITING_FOR_USER",
                task_id=f"{self.execution_mode}-task",
                needs_user_input=True,
                requested_fields=["product_name"],
            )
        return ExecutionResult(
            execution_mode=self.execution_mode,
            answer="Cam-A1 的保修期为 1 年",
            status="SUCCEEDED",
            task_id=request.task_id,
            trace_ref=f"trace:{request.task_id}",
            verification_executed=True,
        )


def test_runner_resumes_one_task_with_structured_scripted_user_input():
    case = EvalCase(
        case_id="warranty-clarification-001",
        version="v1",
        slice="warranty",
        complex_task=True,
        initial_message="查询产品保修",
        expected_boundary="agent",
        required_facts=["Cam-A1", "1 年"],
        expected_requested_fields=["product_name"],
        scripted_user_inputs={"product_name": "Cam-A1"},
    )
    workflow = ClarifyingExecutor("workflow")
    agent = ClarifyingExecutor("agent")
    auto = ClarifyingExecutor("agent")

    results = EvalRunner(
        workflow_executor=workflow,
        agent_executor=agent,
        auto_executor=auto,
    ).run_case(case)

    assert all(result.success for result in results)
    for executor in (workflow, agent, auto):
        initial, resumed = executor.requests
        assert resumed.task_id == f"{executor.execution_mode}-task"
        assert resumed.scripted_user_input == {"product_name": "Cam-A1"}


from app.agent.domain import ToolResult, ToolStatus


class SuccessfulToolAdapter:
    tool_schemas = {"graph_lookup": {}}

    def __init__(self):
        self.calls = 0

    def execute(self, tool_name, arguments):
        assert tool_name == "graph_lookup"
        assert arguments == {"operation": "warranty", "product_a": "Cam-A1"}
        self.calls += 1
        return ToolResult(status=ToolStatus.OK)


class RetryingExecutor:
    def __init__(self, execution_mode):
        self.execution_mode = execution_mode

    def run(self, request):
        first = request.tool_adapter.execute(
            "graph_lookup", {"operation": "warranty", "product_a": "Cam-A1"}
        )
        assert first.retryable is True
        second = request.tool_adapter.execute(
            "graph_lookup", {"operation": "warranty", "product_a": "Cam-A1"}
        )
        assert second.status is ToolStatus.OK
        return ExecutionResult(
            execution_mode=self.execution_mode,
            answer="Cam-A1 的保修期为 1 年",
            status="SUCCEEDED",
            verification_executed=True,
        )


def test_runner_injects_eval_only_retryable_fault_and_grades_process_limits():
    case = EvalCase(
        case_id="warranty-retry-001",
        version="v1",
        slice="recovery",
        complex_task=True,
        initial_message="查询 Cam-A1 的保修",
        expected_boundary="agent",
        required_facts=["Cam-A1", "1 年"],
        required_capabilities=["graph_lookup"],
        forbidden_capabilities=["exact_lookup"],
        fault_injection={"kind": "retryable_error_first", "tool_name": "graph_lookup"},
        max_tool_calls=2,
        max_tool_retries=1,
    )
    adapters = []

    def adapter_factory(_case, _fixture):
        adapter = SuccessfulToolAdapter()
        adapters.append(adapter)
        return adapter

    results = EvalRunner(
        workflow_executor=RetryingExecutor("workflow"),
        agent_executor=RetryingExecutor("agent"),
        auto_executor=RetryingExecutor("agent"),
        tool_adapter_factory=adapter_factory,
    ).run_case(case)

    assert all(result.success for result in results)
    assert all(result.grader_details["process_limits"].passed for result in results)
    assert all(result.grader_details["process_capabilities"].passed for result in results)
    assert all(adapter.calls == 1 for adapter in adapters)


from app.evaluation.run_harness import main


def test_cli_writes_jsonl_results_from_a_versioned_case_file(tmp_path):
    repository_root = Path(__file__).resolve().parents[2]
    case = load_cases(repository_root / "evaluation/agent_v2_seed_cases.jsonl")[0]
    cases_path = tmp_path / "cases.jsonl"
    results_path = tmp_path / "results.jsonl"
    cases_path.write_text(case.model_dump_json() + "\n", encoding="utf-8")

    exit_code = main(
        [
            "--cases", str(cases_path),
            "--executor-factory",
            "app.evaluation.production_runner:build_production_runner",
            "--output", str(results_path),
        ]
    )

    rows = [
        json.loads(line)
        for line in results_path.read_text(encoding="utf-8").splitlines()
    ]
    assert exit_code == 0
    assert len(rows) == 3
    assert all(row["events"][-1]["event_type"] == "EVAL_RESULT" for row in rows)
    assert next(row for row in rows if row["requested_mode"] == "agent")[
        "trace_refs"
    ][0].startswith("agent_run:")


class UnsupportedSuccessExecutor:
    def run(self, _request):
        return ExecutionResult(
            execution_mode="workflow",
            answer="Cam-A1 的保修期为 1 年",
            status="SUCCEEDED",
            supported=False,
            verification_executed=False,
            failure_category="VERIFICATION",
        )


def test_graders_reject_unsupported_unverified_success_and_auto_boundary_mismatch():
    case = EvalCase(
        case_id="unsupported-success-001",
        version="v1",
        slice="verification",
        complex_task=True,
        initial_message="动态查询",
        expected_boundary="agent",
        required_facts=["Cam-A1", "1 年"],
    )
    results = EvalRunner(
        workflow_executor=UnsupportedSuccessExecutor(),
        agent_executor=UnsupportedSuccessExecutor(),
        auto_executor=UnsupportedSuccessExecutor(),
    ).run_case(case)

    assert all(result.success is False for result in results)
    assert all(
        result.grader_details["outcome_supported_success"].passed is False
        for result in results
    )
    assert results[1].grader_details["process_verification"].passed is False
    assert results[0].grader_details["process_verification"].passed is True
    assert results[2].grader_details["process_verification"].passed is True
    assert results[-1].grader_details["boundary"].passed is False
    assert results[-1].failure_category == "VERIFICATION"


class WrongModeExecutor:
    def __init__(self, execution_mode):
        self.execution_mode = execution_mode

    def run(self, _request):
        return ExecutionResult(
            execution_mode=self.execution_mode,
            answer="已完成",
            status="SUCCEEDED",
            verification_executed=True,
        )


def test_forced_modes_fail_when_executor_reports_the_other_execution_mode():
    case = EvalCase(
        case_id="forced-mode-001",
        version="v1",
        slice="routing",
        complex_task=False,
        initial_message="查询",
        expected_boundary="workflow",
    )
    results = EvalRunner(
        workflow_executor=WrongModeExecutor("agent"),
        agent_executor=WrongModeExecutor("agent"),
        auto_executor=WrongModeExecutor("workflow"),
    ).run_case(case)

    assert results[0].grader_details["boundary"].passed is False
    assert results[0].success is False
    assert results[1].grader_details["boundary"].passed is True
    assert results[2].grader_details["boundary"].passed is True


def test_versioned_seed_and_regression_sets_report_spec_metrics(tmp_path):
    repository_root = Path(__file__).resolve().parents[2]
    seed_cases = load_cases(repository_root / "evaluation/agent_v2_seed_cases.jsonl")
    regression_cases = load_cases(
        repository_root / "evaluation/agent_v2_regression_cases.jsonl"
    )
    report_path = tmp_path / "report.json"
    results_path = tmp_path / "results.jsonl"

    exit_code = main(
        [
            "--cases",
            str(repository_root / "evaluation/agent_v2_seed_cases.jsonl"),
            "--regression-cases",
            str(repository_root / "evaluation/agent_v2_regression_cases.jsonl"),
            "--executor-factory",
            "app.evaluation.production_runner:build_production_runner",
            "--output",
            str(results_path),
            "--report",
            str(report_path),
        ]
    )

    distribution = {}
    for case in seed_cases:
        distribution[case.slice] = distribution.get(case.slice, 0) + 1
    rows = [
        json.loads(line)
        for line in results_path.read_text(encoding="utf-8").splitlines()
    ]
    report = json.loads(report_path.read_text(encoding="utf-8"))

    assert len(seed_cases) == 24
    assert distribution == {
        "simple-deterministic": 4,
        "boundary": 4,
        "complex-dynamic-path": 6,
        "missing-information": 4,
        "tool-retrieval-failure": 3,
        "verification": 3,
    }
    assert {case.source for case in regression_cases} == {"runtime_badcase"}
    assert len(rows) == 75
    assert exit_code == (0 if all(row["expectation_met"] for row in rows) else 1)
    assert report["seed_case_count"] == 24
    assert report["regression_case_count"] == 1
    assert 0.0 <= report["workflow_complex_task_success_rate"] <= 1.0
    assert 0.0 <= report["agent_complex_task_success_rate"] <= 1.0
    assert report["agent_complex_task_improvement_supported"] is (
        report["agent_complex_task_success_rate"]
        > report["workflow_complex_task_success_rate"]
    )
    assert report["diagnostics"]["tool_retry_count"] > 0
    assert report["diagnostics"]["verification_rejection_count"] > 0
    assert report["diagnostics"]["tool_call_count"] > 0
    assert all(
        row["trace_refs"]
        for row in rows
        if row["requested_mode"] == "agent"
    )
    assert all(
        row["origin_trace_ref"]
        for row in rows
        if row["case_id"] == "runtime-badcase-retry-001"
    )


def test_every_dynamic_case_derives_the_next_tool_arguments_from_observation():
    repository_root = Path(__file__).resolve().parents[2]
    dynamic_cases = [
        case
        for case in load_cases(
            repository_root / "evaluation/agent_v2_seed_cases.jsonl"
        )
        if case.slice == "complex-dynamic-path"
    ]
    assert len(dynamic_cases) == 6

    runner = build_production_runner()
    for case in dynamic_cases:
        _workflow, agent, auto = runner.run_case(case)
        tool_calls = [
            event.payload
            for event in agent.events
            if event.event_type == "TOOL_CALL"
        ]
        tool_results = [
            event.payload
            for event in agent.events
            if event.event_type == "TOOL_RESULT"
        ]
        assert tool_calls[0]["tool_name"] == "exact_lookup"
        assert len(tool_calls) >= 2
        downstream_products = [
            value
            for key, value in tool_calls[1]["arguments"].items()
            if key.startswith("product_")
        ]
        first_observation = json.dumps(
            tool_results[0]["observation_summary"], ensure_ascii=False
        )
        assert downstream_products
        assert all(value not in case.initial_message for value in downstream_products)
        assert all(value in first_observation for value in downstream_products)
        assert agent.success is True
        assert auto.execution_mode == "agent"

    regression_case = load_cases(
        repository_root / "evaluation/agent_v2_regression_cases.jsonl"
    )[0]
    _workflow, regression_agent, _auto = runner.run_case(regression_case)
    assert regression_agent.success is True
    assert regression_agent.tool_retry_count == 1
    assert regression_agent.origin_trace_ref == regression_case.badcase_trace_ref


def test_production_runner_uses_real_workflow_agent_and_auto_router_paths():
    case = EvalCase(
        case_id="production-paths-001",
        version="v1",
        slice="complex-dynamic-path",
        complex_task=True,
        initial_message="先查出错误码 E1001 对应的产品，再查询该产品保修。",
        expected_boundary="agent",
        required_capabilities=["exact_lookup", "graph_lookup"],
        required_facts=["Cam-A1", "1 年"],
        fixture={
            "tool_results": [
                {
                    "tool_name": "exact_lookup",
                    "arguments": {"entity_type": "error_code", "identifier": "E1001"},
                    "result": {
                        "status": "OK",
                        "data": {
                            "entities": [
                                {"attributes": {"product_sku": "Cam-A1"}}
                            ]
                        },
                    },
                },
                {
                    "tool_name": "graph_lookup",
                    "arguments": {
                        "operation": "warranty",
                        "product_a": "Cam-A1",
                    },
                    "result": {
                        "status": "OK",
                        "evidence": [
                            {
                                "evidence_id": "neo4j:warranty:Cam-A1",
                                "kind": "graph",
                                "text": "Cam-A1 适用标准保修，保修期 1 年。",
                                "citation": {"source_type": "fixture"},
                            }
                        ],
                    },
                },
            ]
        },
    )

    workflow, agent, auto = build_production_runner().run_case(case)

    assert [workflow.execution_mode, agent.execution_mode, auto.execution_mode] == [
        "workflow",
        "agent",
        "agent",
    ]
    assert workflow.success is False
    assert agent.success is True
    assert auto.success is True
    assert agent.trace_refs[0].startswith("agent_run:")
    agent_tool_calls = [
        event.payload for event in agent.events if event.event_type == "TOOL_CALL"
    ]
    assert [call["tool_name"] for call in agent_tool_calls] == [
        "exact_lookup",
        "graph_lookup",
    ]
    assert agent_tool_calls[1]["arguments"]["product_a"] == "Cam-A1"


class ExpectedFailureExecutor:
    def __init__(self, execution_mode):
        self.execution_mode = execution_mode

    def run(self, _request):
        return ExecutionResult(
            execution_mode=self.execution_mode,
            status="FAILED",
            failure_category="TOOL_OR_RETRIEVAL",
        )


def test_expected_failures_do_not_inflate_complex_task_success_rate():
    case = EvalCase(
        case_id="expected-failure-001",
        version="v1",
        slice="tool-retrieval-failure",
        complex_task=True,
        initial_message="查询不存在的错误码 E9999",
        expected_boundary="agent",
        expected_status="FAILED",
        requires_verification=False,
    )
    results = EvalRunner(
        workflow_executor=ExpectedFailureExecutor("workflow"),
        agent_executor=ExpectedFailureExecutor("agent"),
        auto_executor=ExpectedFailureExecutor("agent"),
    ).run_case(case)

    assert all(result.success is False for result in results)
    assert all(result.expectation_met is True for result in results)
    assert all(result.expected_failure_matched is True for result in results)
    summary = summarize_results([case], results)
    assert summary.workflow_complex_task_success_rate == 0.0
    assert summary.agent_complex_task_success_rate == 0.0
    assert summary.expected_failure_match_rate == 1.0


def test_summary_distinguishes_boundary_slice_from_all_case_auto_routing_accuracy():
    boundary_case = EvalCase(
        case_id="boundary-workflow-001",
        version="v1",
        slice="boundary",
        complex_task=False,
        initial_message="Cam-A1 保修多久？",
        expected_boundary="workflow",
    )
    missing_case = EvalCase(
        case_id="missing-agent-001",
        version="v1",
        slice="missing-information",
        complex_task=True,
        initial_message="查询产品保修。",
        expected_boundary="agent",
    )
    runner = EvalRunner(
        workflow_executor=WrongModeExecutor("workflow"),
        agent_executor=WrongModeExecutor("agent"),
        auto_executor=WrongModeExecutor("workflow"),
    )

    summary = summarize_results(
        [boundary_case, missing_case],
        runner.run_cases([boundary_case, missing_case]),
    )

    assert summary.boundary_slice_accuracy == 1.0
    assert summary.auto_routing_accuracy == 0.5
    assert not hasattr(summary, "boundary_accuracy")


def test_regression_case_rejects_static_file_as_badcase_trace_ref():
    with pytest.raises(ValidationError):
        EvalCase(
            case_id="runtime-badcase-static-001",
            version="v1",
            source="runtime_badcase",
            badcase_trace_ref="evaluation/badcase.json",
            # Static files cannot stand in for a persisted AgentRun trace.
            slice="failure",
            complex_task=True,
            initial_message="查询",
            expected_boundary="agent",
        )


def test_regression_result_links_origin_and_current_real_run_trace():
    case = EvalCase(
        case_id="runtime-badcase-001",
        version="v1",
        source="runtime_badcase",
        badcase_trace_ref="agent_run:8f0c5731-f6cb-4d69-92ca-81d9726a46bf",
        slice="tool-retrieval-failure",
        complex_task=True,
        initial_message="先查错误码 E9999 对应产品，再查询该产品保修。",
        expected_boundary="agent",
        expected_status="FAILED",
        requires_verification=False,
    )

    _workflow, agent, _auto = build_production_runner().run_case(case)

    assert agent.expectation_met is True
    assert agent.failure_category == "TOOL_OR_RETRIEVAL"
    assert agent.origin_trace_ref == "agent_run:8f0c5731-f6cb-4d69-92ca-81d9726a46bf"
    assert agent.trace_refs[0].startswith("agent_run:")
    assert agent.trace_refs[0] != agent.origin_trace_ref
    assert any(
        event.event_type == "RUN_COMPLETED"
        and event.payload["failure_category"] == "TOOL_OR_RETRIEVAL"
        for event in agent.events
    )

def test_resumed_eval_trace_contains_each_real_event_once():
    repository_root = Path(__file__).resolve().parents[2]
    case = next(
        case
        for case in load_cases(
            repository_root / "evaluation/agent_v2_seed_cases.jsonl"
        )
        if case.case_id == "missing-warranty-001"
    )

    _workflow, agent, _auto = build_production_runner().run_case(case)

    ask_user_events = [
        event
        for event in agent.events
        if event.event_type == "MODEL_DECISION"
        and event.payload.get("decision") == "ASK_USER"
    ]
    sequence_numbers = [
        event.payload["sequence_number"]
        for event in agent.events
        if "sequence_number" in event.payload
    ]
    assert len(ask_user_events) == 1
    assert sequence_numbers == sorted(set(sequence_numbers))
    assert len(agent.trace_refs) == 1
