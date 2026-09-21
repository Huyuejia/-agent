"""RetrievalPlan 执行器：依赖编排、未命中策略与 RRF 融合。"""

from __future__ import annotations

from enum import Enum
from typing import Mapping

from pydantic import BaseModel, Field

from app.retrieval.domain import (
    Evidence,
    FusionStrategy,
    MissPolicy,
    RetrievalMode,
    RetrievalPlan,
    RetrievalStep,
)
from app.retrieval.hybrid_retriever import HybridRetriever
from app.retrieval.retriever import Retriever


class RetrievalExecutionError(RuntimeError):
    """计划无效、检索器缺失或检索后端失败。"""


class ExecutionStatus(str, Enum):
    COMPLETED = "completed"
    STOPPED = "stopped"
    FALLBACK = "fallback"
    NEEDS_CONTEXT = "needs_context"
    NEEDS_CLARIFICATION = "needs_clarification"


class StepStatus(str, Enum):
    COMPLETED = "completed"
    MISS = "miss"
    SKIPPED = "skipped"


class StepExecutionResult(BaseModel):
    step_id: str
    mode: RetrievalMode
    status: StepStatus
    evidence: list[Evidence] = Field(default_factory=list)
    message: str | None = None


class RetrievalExecutionResult(BaseModel):
    status: ExecutionStatus
    evidence: list[Evidence] = Field(default_factory=list)
    steps: list[StepExecutionResult] = Field(default_factory=list)
    stopped_at: str | None = None
    clarification_question: str | None = None


class RetrievalExecutor:
    """按计划执行已注册 Retriever。

    MissPolicy 只描述“正常执行但零命中”；后端异常不会伪装成 miss，而是包装为
    RetrievalExecutionError 交给更上层的可观测降级策略处理。
    """

    def __init__(
        self,
        retrievers: Mapping[RetrievalMode, Retriever],
        hybrid_retriever: HybridRetriever | None = None,
    ) -> None:
        self._retrievers = dict(retrievers)
        self._hybrid = hybrid_retriever or HybridRetriever()

    def execute(self, query: str, plan: RetrievalPlan) -> RetrievalExecutionResult:
        if plan.requires_clarification:
            return RetrievalExecutionResult(
                status=ExecutionStatus.NEEDS_CLARIFICATION,
                clarification_question=plan.clarification_question,
            )
        if plan.requires_context:
            return RetrievalExecutionResult(status=ExecutionStatus.NEEDS_CONTEXT)

        ordered_steps = self._topological_steps(plan.steps)
        results: list[StepExecutionResult] = []
        results_by_id: dict[str, StepExecutionResult] = {}

        for step in ordered_steps:
            dependency_results = [results_by_id[item] for item in step.depends_on]
            unavailable_dependencies = [
                item
                for item in dependency_results
                if item.status is not StepStatus.COMPLETED or not item.evidence
            ]
            if unavailable_dependencies:
                skipped = StepExecutionResult(
                    step_id=step.step_id,
                    mode=step.mode,
                    status=StepStatus.SKIPPED,
                    message="依赖步骤未产生证据",
                )
                results.append(skipped)
                results_by_id[step.step_id] = skipped
                if step.required:
                    return self._result(
                        ExecutionStatus.STOPPED,
                        plan,
                        results,
                        stopped_at=step.step_id,
                    )
                continue

            executable_step = self._with_dependency_filters(step, dependency_results)
            retriever = self._retrievers.get(step.mode)
            if retriever is None:
                raise RetrievalExecutionError(
                    f"步骤 {step.step_id} 未注册 {step.mode.value} Retriever"
                )

            try:
                evidence = retriever.retrieve(query, executable_step)
            except Exception as exc:
                raise RetrievalExecutionError(
                    f"步骤 {step.step_id} ({step.mode.value}) 执行失败"
                ) from exc

            step_result = StepExecutionResult(
                step_id=step.step_id,
                mode=step.mode,
                status=StepStatus.COMPLETED if evidence else StepStatus.MISS,
                evidence=evidence,
            )
            results.append(step_result)
            results_by_id[step.step_id] = step_result

            if evidence:
                continue
            if step.miss_policy is MissPolicy.STOP:
                return self._result(
                    ExecutionStatus.STOPPED,
                    plan,
                    results,
                    stopped_at=step.step_id,
                )
            if step.miss_policy is MissPolicy.FALLBACK:
                return self._result(
                    ExecutionStatus.FALLBACK,
                    plan,
                    results,
                    stopped_at=step.step_id,
                )

        return self._result(ExecutionStatus.COMPLETED, plan, results)

    def _result(
        self,
        status: ExecutionStatus,
        plan: RetrievalPlan,
        results: list[StepExecutionResult],
        *,
        stopped_at: str | None = None,
    ) -> RetrievalExecutionResult:
        return RetrievalExecutionResult(
            status=status,
            evidence=self._final_evidence(plan, results),
            steps=results,
            stopped_at=stopped_at,
        )

    def _final_evidence(
        self,
        plan: RetrievalPlan,
        results: list[StepExecutionResult],
    ) -> list[Evidence]:
        if plan.fusion_strategy is FusionStrategy.RRF:
            fusion_inputs = {
                item.step_id: item.evidence
                for item in results
                if item.mode in {RetrievalMode.LEXICAL, RetrievalMode.VECTOR}
                and item.evidence
            }
            return self._hybrid.fuse(fusion_inputs, top_k=plan.output_top_k)

        deduplicated: list[Evidence] = []
        seen: set[str] = set()
        for result in results:
            for evidence in result.evidence:
                if evidence.evidence_id not in seen:
                    seen.add(evidence.evidence_id)
                    deduplicated.append(evidence)
        return deduplicated[: plan.output_top_k]

    @staticmethod
    def _with_dependency_filters(
        step: RetrievalStep,
        dependencies: list[StepExecutionResult],
    ) -> RetrievalStep:
        if not dependencies:
            return step

        evidence = [item for dependency in dependencies for item in dependency.evidence]
        evidence_ids = list(dict.fromkeys(item.evidence_id for item in evidence))
        entity_ids = list(
            dict.fromkeys(entity_id for item in evidence for entity_id in item.entity_ids)
        )
        filters = dict(step.filters)
        resolved_entities = []
        for item in evidence:
            payload = item.citation.payload
            identifier = payload.get("identifier")
            table = payload.get("table")
            record_id = payload.get("record_id")
            if identifier and table:
                resolved_entities.append(
                    {
                        "identifier": identifier,
                        "table": table,
                        "record_id": str(record_id or ""),
                    }
                )
        filters["dependency_evidence_ids"] = evidence_ids
        filters["resolved_entity_ids"] = entity_ids
        filters["resolved_entities"] = resolved_entities
        return step.model_copy(update={"filters": filters})

    @staticmethod
    def _topological_steps(steps: list[RetrievalStep]) -> list[RetrievalStep]:
        by_id: dict[str, RetrievalStep] = {}
        for step in steps:
            if step.step_id in by_id:
                raise RetrievalExecutionError(f"检索计划包含重复 step_id：{step.step_id}")
            by_id[step.step_id] = step

        for step in steps:
            for dependency in step.depends_on:
                if dependency not in by_id:
                    raise RetrievalExecutionError(
                        f"步骤 {step.step_id} 依赖不存在的步骤：{dependency}"
                    )

        ordered: list[RetrievalStep] = []
        state: dict[str, int] = {}

        def visit(step_id: str) -> None:
            if state.get(step_id) == 1:
                raise RetrievalExecutionError("检索计划存在循环依赖")
            if state.get(step_id) == 2:
                return
            state[step_id] = 1
            step = by_id[step_id]
            for dependency in step.depends_on:
                visit(dependency)
            state[step_id] = 2
            ordered.append(step)

        for step in steps:
            visit(step.step_id)
        return ordered
