import json
from pathlib import Path

from app.evaluation.harness import EvalCase, EvalRunner, ExecutionResult, load_cases
from app.evaluation.fixture_runner import build_deterministic_runner

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
    assert [request.fixture for request in workflow.requests + agent.requests + auto.requests] == [{}, {}, {}]


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
    cases_path = tmp_path / "cases.jsonl"
    results_path = tmp_path / "results.jsonl"
    cases_path.write_text(
        json.dumps(
            {
                "case_id": "cli-001",
                "version": "v1",
                "slice": "smoke",
                "complex_task": False,
                "initial_message": "查询 Cam-A1 的保修",
                "expected_boundary": "workflow",
                "required_facts": ["Cam-A1", "1 年"],
                "fixture": {
                    "deterministic_outcomes": {
                        "workflow": {"answer": "Cam-A1 的保修期为 1 年", "status": "SUCCEEDED"},
                        "agent": {"answer": "Cam-A1 的保修期为 1 年", "status": "SUCCEEDED"},
                        "auto": {"answer": "Cam-A1 的保修期为 1 年", "status": "SUCCEEDED", "execution_mode": "workflow"},
                    }
                },
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    exit_code = main(
        [
            "--cases", str(cases_path),
            "--executor-factory", "app.evaluation.fixture_runner:build_deterministic_runner",
            "--output", str(results_path),
        ]
    )

    rows = [json.loads(line) for line in results_path.read_text(encoding="utf-8").splitlines()]
    assert exit_code == 0
    assert len(rows) == 3
    assert all(row["events"][-1]["event_type"] == "EVAL_RESULT" for row in rows)


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
    assert all(
        result.grader_details["process_verification"].passed is False
        for result in results
    )
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
            "app.evaluation.fixture_runner:build_deterministic_runner",
            "--output",
            str(results_path),
            "--report",
            str(report_path),
        ]
    )

    distribution = {}
    for case in seed_cases:
        distribution[case.slice] = distribution.get(case.slice, 0) + 1

    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert exit_code == 0
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
    assert report["seed_case_count"] == 24
    assert report["regression_case_count"] == 1
    assert report["workflow_complex_task_success_rate"] == 1.0
    assert report["agent_complex_task_success_rate"] == 1.0
    assert report["boundary_accuracy"] == 1.0
    assert report["simple_over_agentization_rate"] == 0.0
    assert report["agent_complex_task_improvement_supported"] is False
    assert report["agent_complex_task_hypothesis"].startswith("not validated:")
    assert report["diagnostics"]["tool_retry_count"] >= 6
    assert report["diagnostics"]["verification_rejection_count"] >= 9
    assert report["diagnostics"]["tool_call_count"] > 0
    assert all(
        json.loads(result)["success"]
        for result in results_path.read_text(encoding="utf-8").splitlines()
    )


def test_dynamic_observation_case_and_retry_badcase_are_process_graded():
    repository_root = Path(__file__).resolve().parents[2]
    seed_cases = load_cases(repository_root / "evaluation/agent_v2_seed_cases.jsonl")
    regression_case = load_cases(
        repository_root / "evaluation/agent_v2_regression_cases.jsonl"
    )[0]
    runner = build_deterministic_runner()
    dynamic_case = next(
        case for case in seed_cases if case.case_id == "complex-observation-warranty-001"
    )

    dynamic_results = runner.run_case(dynamic_case)
    regression_results = runner.run_case(regression_case)

    assert all(
        result.grader_details["process_tool_arguments"].passed
        for result in dynamic_results
    )
    assert all(result.tool_call_count == 2 for result in dynamic_results)
    assert all(result.success for result in regression_results)
    assert all(result.tool_retry_count == 1 for result in regression_results)
    assert all(
        result.grader_details["process_required_retries"].passed
        for result in regression_results
    )
