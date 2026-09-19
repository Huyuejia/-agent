"""Production-path Workflow/Agent executors for deterministic local Eval."""

from __future__ import annotations

from copy import deepcopy
import re
import uuid
from dataclasses import dataclass
from typing import Literal

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.agent.domain import (
    AgentCandidate,
    ExecutionDecision,
    ExecutionMode,
    RuntimeResult,
    TaskStatus,
    ToolResult,
    ToolStatus,
)
from app.agent.routing import ExecutionRouter
from app.agent.service import AgentTaskService
from app.agent.tools import AgentToolAdapter
from app.evaluation.harness import (
    EvalCase,
    EvalEvent,
    EvalFixture,
    EvalRunner,
    ExecutionRequest,
    ExecutionResult,
)
from app.models.agent_run import AgentRun, AgentTraceEvent
from app.models.base import Base
from app.models.conversation import Conversation, Message
from app.models.user import User
from app.services.chat_orchestrator import ChatOrchestrator


class FixtureToolAdapter:
    """Serve production-schema ToolResults from versioned Eval tool data."""

    tool_schemas = AgentToolAdapter.tool_schemas

    def __init__(self, fixture: EvalFixture) -> None:
        self._responses = list(fixture.tool_results)

    def execute(self, tool_name: str, arguments: dict) -> ToolResult:
        for response in self._responses:
            if response.tool_name == tool_name and response.arguments == arguments:
                return deepcopy(response.result)
        return ToolResult(
            status=ToolStatus.ERROR,
            error_code="EVAL_TOOL_DATA_MISSING",
            retryable=False,
        )


class _ForcedRouter:
    def __init__(self, mode: ExecutionMode) -> None:
        self._mode = mode

    def decide(self, _message: str) -> ExecutionDecision:
        return ExecutionDecision(mode=self._mode, reason_code=f"eval_forced_{self._mode.value}")


class _WorkflowGraph:
    """Expose the production Workflow graph port over the shared Eval Tool adapter."""

    def __init__(self, tools) -> None:
        self._tools = tools

    def check_compatibility(self, product_a: str, product_b: str) -> bool:
        result = self._tools.execute(
            "graph_lookup",
            {"operation": "compatibility", "product_a": product_a, "product_b": product_b},
        )
        self._require_ok(result)
        return bool(result.data.get("compatible"))

    def get_protocols(self, product_name: str) -> list[str]:
        result = self._tools.execute(
            "graph_lookup",
            {"operation": "protocols", "product_a": product_name},
        )
        self._require_ok(result)
        return list(result.data.get("protocols", []))

    def get_warranty(self, product_name: str) -> dict | None:
        result = self._tools.execute(
            "graph_lookup",
            {"operation": "warranty", "product_a": product_name},
        )
        if result.status is ToolStatus.NOT_FOUND:
            return None
        self._require_ok(result)
        warranty = result.data.get("warranty")
        return warranty if isinstance(warranty, dict) else None

    @staticmethod
    def _require_ok(result: ToolResult) -> None:
        if result.status is not ToolStatus.OK:
            raise ValueError(result.error_code or result.status.value)


class _WorkflowKnowledge:
    def __init__(self, tools) -> None:
        self._tools = tools

    def search(self, query: str, top_k: int = 3) -> dict:
        result = self._tools.execute(
            "knowledge_search",
            {"query": query, "top_k": top_k},
        )
        sources = [
            {
                "document_name": evidence.citation.payload.get("document_name"),
                "location": evidence.citation.payload.get("location"),
                "snippet": evidence.text,
            }
            for evidence in result.evidence
        ]
        return {
            "answer": "\n".join(evidence.text for evidence in result.evidence),
            "sources": sources,
        }


class _EvalRuntime:
    """Deterministic external-model boundary driven only by state and observations."""

    runtime_version = "eval-model-boundary-v1"
    model_identifier = "eval/deterministic"
    prompt_version = "agent-v2-eval-v1"
    _PRODUCTS = ("Cam-A1", "Hub-Z1", "Sensor-T1", "Lock-D1", "Light-B1", "Plug-P1")

    def run(self, *, objective, task_state, execute_tool, emit_event, **_kwargs):
        if "无证据候选" in objective:
            return RuntimeResult(
                candidate=AgentCandidate(
                    answer="未经证据支持的候选答案",
                    source_type="knowledge_graph",
                    evidence_refs=[],
                )
            )
        if "未观察证据" in objective or "修复后仍拒绝" in objective:
            return RuntimeResult(
                candidate=AgentCandidate(
                    answer="引用了未观察证据的候选答案",
                    source_type="knowledge_graph",
                    evidence_refs=["fabricated:evidence"],
                )
            )

        fields = self._known_fields(task_state)
        products = [
            product
            for product in self._PRODUCTS
            if product in objective or product in fields.values()
        ]
        identifiers = self._identifiers(objective, fields)
        requested_fields = self._requested_fields(objective, products, identifiers)
        if requested_fields:
            emit_event(
                "MODEL_DECISION",
                {"decision": "ASK_USER", "requested_fields": requested_fields},
            )
            return RuntimeResult(
                clarification_text="请补充：" + "、".join(requested_fields),
                requested_fields=requested_fields,
            )

        evidence = []
        if identifiers:
            entity_type, identifier = identifiers[0]
            exact = self._call(
                emit_event,
                execute_tool,
                "exact_lookup",
                {"entity_type": entity_type, "identifier": identifier},
            )
            if exact.status is not ToolStatus.OK:
                return RuntimeResult(error_code=exact.error_code or "TOOL_NOT_FOUND")
            evidence.extend(exact.evidence)
            attributes = self._first_attributes(exact)
            observed_products = attributes.get("product_skus")
            if not isinstance(observed_products, list):
                observed_product = attributes.get("product_sku")
                observed_products = [observed_product] if observed_product else []
            products = [str(product) for product in observed_products]
            if "兼容" in objective:
                if len(products) < 2:
                    return RuntimeResult(error_code="STATE_LOSS")
                result = self._call(
                    emit_event,
                    execute_tool,
                    "graph_lookup",
                    {
                        "operation": "compatibility",
                        "product_a": products[0],
                        "product_b": products[1],
                    },
                )
                evidence.extend(result.evidence)
            elif "协议" in objective:
                if not products:
                    return RuntimeResult(error_code="STATE_LOSS")
                result = self._call(
                    emit_event,
                    execute_tool,
                    "graph_lookup",
                    {"operation": "protocols", "product_a": products[0]},
                )
                evidence.extend(result.evidence)
            elif "保修" in objective:
                if not products:
                    return RuntimeResult(error_code="STATE_LOSS")
                result = self._call(
                    emit_event,
                    execute_tool,
                    "graph_lookup",
                    {"operation": "warranty", "product_a": products[0]},
                )
                evidence.extend(result.evidence)
        elif "兼容" in objective and len(products) >= 2:
            result = self._call(
                emit_event,
                execute_tool,
                "graph_lookup",
                {
                    "operation": "compatibility",
                    "product_a": products[0],
                    "product_b": products[1],
                },
            )
            evidence.extend(result.evidence)
        elif "协议" in objective and products:
            result = self._call(
                emit_event,
                execute_tool,
                "graph_lookup",
                {"operation": "protocols", "product_a": products[0]},
            )
            evidence.extend(result.evidence)
        elif "保修" in objective and products:
            result = self._call(
                emit_event,
                execute_tool,
                "graph_lookup",
                {"operation": "warranty", "product_a": products[0]},
            )
            evidence.extend(result.evidence)
        else:
            return RuntimeResult(error_code="TASK_UNDERSTANDING")

        if not evidence:
            return RuntimeResult(error_code="TOOL_NOT_FOUND")
        emit_event("MODEL_DECISION", {"decision": "FINALIZE"})
        return RuntimeResult(
            candidate=AgentCandidate(
                answer=" ".join(item.text for item in evidence),
                source_type="agent_evidence",
                evidence_refs=[item.evidence_id for item in evidence],
            )
        )

    def repair(self, *, objective, **_kwargs):
        refs = [] if "无证据候选" in objective else ["fabricated:repair"]
        return RuntimeResult(
            candidate=AgentCandidate(
                answer="修复后仍未通过证据校验",
                source_type="knowledge_graph",
                evidence_refs=refs,
            )
        )

    @staticmethod
    def _known_fields(task_state) -> dict[str, str]:
        fields: dict[str, str] = {}
        for fact in task_state.known_facts:
            values = fact.get("fields")
            if isinstance(values, dict):
                fields.update(
                    {
                        str(key): str(value)
                        for key, value in values.items()
                        if value is not None
                    }
                )
        return fields

    @staticmethod
    def _identifiers(objective: str, fields: dict[str, str]):
        values = [objective, *fields.values()]
        patterns = (
            ("error_code", r"E\d{3,}"),
            ("order", r"ORD[-:]?\d{4,}"),
            ("serial", r"SN[:：-]?[A-Z0-9]{6,}"),
        )
        for entity_type, pattern in patterns:
            for value in values:
                match = re.search(pattern, value, re.IGNORECASE)
                if match:
                    return [(entity_type, match.group(0).upper())]
        return []

    @staticmethod
    def _requested_fields(objective: str, products: list[str], identifiers):
        if "这两个产品" in objective and len(products) < 2:
            return ["product_a", "product_b"]
        if ("报错" in objective or "错误" in objective) and not identifiers:
            return ["error_code"]
        if ("保修" in objective or "协议" in objective) and not products and not identifiers:
            return ["product_name"]
        return []

    @staticmethod
    def _first_attributes(result: ToolResult) -> dict:
        entities = result.data.get("entities", [])
        if not entities or not isinstance(entities[0], dict):
            return {}
        attributes = entities[0].get("attributes", {})
        return attributes if isinstance(attributes, dict) else {}

    @staticmethod
    def _call(emit_event, execute_tool, tool_name: str, arguments: dict) -> ToolResult:
        emit_event(
            "MODEL_DECISION",
            {
                "decision": "CALL_TOOL",
                "tool_name": tool_name,
                "arguments": arguments,
            },
        )
        return execute_tool(tool_name, arguments)

@dataclass
class _ExecutionContext:
    db: Session
    user_id: int
    conversation_id: int
    orchestrator: ChatOrchestrator
    last_trace_sequence: int = 0


class ProductionEvalExecutor:
    """Run one requested mode through the existing production orchestration services."""

    def __init__(self, requested_mode: Literal["workflow", "agent", "auto"]) -> None:
        self._requested_mode = requested_mode
        self._contexts: dict[str, _ExecutionContext] = {}

    def run(self, request: ExecutionRequest) -> ExecutionResult:
        context = self._contexts.get(request.task_id or "")
        if context is None:
            context = self._new_context(request)
        message = request.case.initial_message
        if request.scripted_user_input:
            message = "；".join(request.scripted_user_input.values())
        response = context.orchestrator.route(
            message=message,
            conversation_id=context.conversation_id,
            user_id=context.user_id,
            request_id=f"eval-{uuid.uuid4().hex}",
            resume_fields=request.scripted_user_input,
            db=context.db,
        )
        run_id = response.get("agent_run_id")
        if run_id:
            self._contexts[run_id] = context
        status = response.get("task_status")
        if status is None:
            status = (
                TaskStatus.FAILED.value
                if response.get("handoff_required")
                else TaskStatus.SUCCEEDED.value
            )
        events: list[EvalEvent] = []
        failure_category = response.get("failure_category")
        if run_id:
            trace_rows = context.db.scalars(
                select(AgentTraceEvent)
                .where(
                    AgentTraceEvent.run_id == run_id,
                    AgentTraceEvent.sequence_number > context.last_trace_sequence,
                )
                .order_by(AgentTraceEvent.sequence_number)
            ).all()
            events = [
                EvalEvent(
                    event_type=row.event_type,
                    payload={**row.payload, "sequence_number": row.sequence_number},
                    error_code=row.error_code,
                )
                for row in trace_rows
            ]
            if trace_rows:
                context.last_trace_sequence = trace_rows[-1].sequence_number
            run = context.db.get(AgentRun, run_id)
            if run is not None:
                failure_category = run.failure_category
        verification_events = [
            event for event in events if event.event_type == "VERIFICATION_RESULT"
        ]
        return ExecutionResult(
            execution_mode=response["execution_mode"],
            answer=response["answer"],
            status=status,
            task_id=run_id,
            trace_ref=f"agent_run:{run_id}" if run_id else None,
            failure_category=failure_category,
            needs_user_input=response.get("needs_user_input", False),
            requested_fields=response.get("requested_fields", []),
            verification_executed=bool(verification_events),
            verification_rejection_count=sum(
                not bool(event.payload.get("accepted")) for event in verification_events
            ),
            supported=(
                status != TaskStatus.SUCCEEDED.value
                or response["execution_mode"] == "workflow"
                or bool(verification_events)
            ),
            events=events,
        )

    def _new_context(self, request: ExecutionRequest) -> _ExecutionContext:
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(
            engine,
            tables=[
                User.__table__,
                Conversation.__table__,
                Message.__table__,
                AgentRun.__table__,
                AgentTraceEvent.__table__,
            ],
        )
        db = sessionmaker(bind=engine, autocommit=False, autoflush=False)()
        user = User(
            email=f"eval-{uuid.uuid4().hex}@example.invalid",
            normalized_email=f"eval-{uuid.uuid4().hex}@example.invalid",
            password_hash="eval-only",
        )
        db.add(user)
        db.flush()
        conversation = Conversation(user_id=user.id, title=request.case.case_id)
        db.add(conversation)
        db.commit()

        tools = request.tool_adapter
        if tools is None:
            raise ValueError("production Eval requires a Tool adapter")
        agent_service = AgentTaskService(
            runtime=_EvalRuntime(),
            tool_adapter_factory=lambda _db: tools,
            max_tool_calls=request.case.max_tool_calls,
            max_tool_retries=request.case.max_tool_retries,
        )
        if self._requested_mode == "workflow":
            router = _ForcedRouter(ExecutionMode.WORKFLOW)
        elif self._requested_mode == "agent":
            router = _ForcedRouter(ExecutionMode.AGENT)
        else:
            router = ExecutionRouter()
        orchestrator = ChatOrchestrator(
            graph_service=_WorkflowGraph(tools),
            rag_service=_WorkflowKnowledge(tools),
            agent_service=agent_service,
            execution_router=router,
        )
        return _ExecutionContext(
            db=db,
            user_id=user.id,
            conversation_id=conversation.id,
            orchestrator=orchestrator,
        )


def build_production_runner() -> EvalRunner:
    return EvalRunner(
        workflow_executor=ProductionEvalExecutor("workflow"),
        agent_executor=ProductionEvalExecutor("agent"),
        auto_executor=ProductionEvalExecutor("auto"),
        tool_adapter_factory=lambda _case, fixture: FixtureToolAdapter(fixture),
    )
