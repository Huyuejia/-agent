# Customer Intelligence Workbench

## Controlled Agent Runtime + Evaluation Spec

**Status:** Proposed
**Scope:** Next engineering iteration
**Primary question:** Can a controlled Agent Runtime improve **Complex Task Success Rate** over the existing deterministic Workflow for observation-dependent customer-service tasks?

---

# 1. Problem Statement

The current `customer-intelligence-workbench` is fundamentally a deterministic, evidence-grounded customer-service retrieval system.

Its current primary execution path is:

```text
QueryAnalyzer
→ RetrievalPlan
→ RetrievalExecutor
→ EXACT / GRAPH / LEXICAL / VECTOR
→ EvidenceAnswerer
```

The existing system is suitable when the retrieval path can be determined before execution: for example, product lookup, compatibility lookup, error-code retrieval, or document search. The current repository does **not** contain a general model-driven `model → tool → observation → continuation` Agent Loop.

The limitation appears when a task contains dependent sub-goals where:

```text
Step N result
        ↓
determines
        ↓
Step N+1 action
```

and the next action cannot be completely decided during initial request analysis.

Typical causes include:

* required information becomes known only after a tool observation;
* a tool observation reveals that another lookup is required;
* information is missing and must be requested from the user;
* one retrieval path fails and the next action depends on the failure type;
* multiple pieces of evidence must be validated before the task can be considered complete.

Trying to encode these tasks entirely through static `RetrievalPlan` branches would progressively increase routing rules and workflow complexity.

This iteration therefore tests the following engineering hypothesis:

> For customer-service tasks whose next action depends on runtime observations, a constrained Agent Runtime can achieve a higher Complex Task Success Rate than the existing deterministic Workflow, while deterministic tasks continue to use the existing Workflow.

This is **not** a proposal to replace the current retrieval architecture with an Agent.

---

# 2. Current System / Baseline

The existing system already provides the deterministic baseline required for this experiment.

Current verified request flow:

```text
React + Vite
    ↓
Go Gateway
    ↓
POST /api/chat
    ↓
FastAPI
    ↓
ChatOrchestrator
    ↓
RetrievalChatService
    ↓
QueryAnalyzer
    ↓
RetrievalExecutor
    ↓
EvidenceAnswerer
    ↓
PostgreSQL message persistence
```

This request chain is present in the current repository and should be extended rather than replaced.

Relevant existing capabilities to reuse:

* PostgreSQL + pgvector;
* Neo4j;
* Redis;
* EXACT retrieval;
* GRAPH retrieval;
* LEXICAL retrieval;
* VECTOR retrieval;
* hybrid/RRF retrieval;
* evidence-oriented answering;
* JWT authentication;
* conversation ownership checks;
* conversation/message persistence;
* existing fallback logic;
* request ID;
* existing Python test suite.

Important current limitations:

* no general Agent Loop;
* no model-controlled Tool calling;
* no execution checkpoint/task state;
* no Agent Tool registry;
* no independent answer verifier;
* no full Agent execution trace;
* no end-to-end Agent evaluation harness;
* historical conversation messages are persisted but are not currently injected into retrieval/model execution.

The existing Workflow is therefore the **baseline**, not legacy code to be removed.

---

# 3. Goals

This iteration SHALL implement the minimum architecture required to evaluate:

```text
Existing Workflow
vs
Controlled Pi-based Agent Runtime
```

for the same customer-service tasks.

Primary engineering goals:

1. Introduce a constrained Agent Runtime for observation-dependent tasks.
2. Preserve the deterministic Workflow for deterministic tasks.
3. Define an explicit Workflow → Agent routing boundary.
4. Integrate Pi without replacing the FastAPI business backend.
5. Introduce task-level state for multi-step/multi-turn Agent tasks.
6. Adapt existing retrieval capabilities into constrained read-only Agent Tools.
7. Implement bounded retry and fallback policies.
8. Prevent the Agent from declaring success without verification.
9. Record a replayable execution trace.
10. Attribute failed Eval cases to a small failure taxonomy.
11. Implement a version-controlled automated Eval Harness.
12. Run the same Eval cases against Workflow and Agent execution.
13. Convert real Agent failures into regression cases.

---

# 4. Non-goals / Out of Scope

The following are explicitly outside this iteration:

* Multi-Agent architecture;
* MCP;
* Electron;
* desktop client;
* SSE/WebSocket migration;
* long-term user memory;
* user profiling;
* vector memory;
* episodic memory;
* autonomous background tasks;
* arbitrary code execution;
* raw SQL/Cypher tools;
* refund execution;
* payment execution;
* order creation;
* order modification;
* order deletion;
* any other externally consequential write operation;
* high-concurrency architecture;
* Kubernetes expansion;
* distributed tracing platform;
* Grafana;
* ELK;
* LangSmith;
* OpenCompass;
* large public benchmarks;
* evaluation UI;
* frontend redesign;
* restoring unrelated features promised by old design documents;
* unrelated repository refactoring.

The current frontend remains an ordinary HTTP/JSON React interface. Streaming is not required for this iteration. The Snapshot confirms that the existing application currently has no SSE/WebSocket execution path.

---

# 5. Core Design Principle

The routing boundary is **not**:

```text
short question → Workflow
long question → Agent
```

and it is not:

```text
single intent → Workflow
multiple intents → Agent
```

The boundary is:

```text
Can the required execution path be determined
before runtime observations are available?
```

If yes:

```text
Workflow
```

If no:

```text
Agent
```

Therefore:

> **Observation-dependent next action is the defining Agent criterion.**

---

# 6. Workflow → Agent Routing Boundary

Introduce a small `ExecutionRouter` / `TaskBoundaryRouter` before the current execution path.

Exact module/file location SHALL be determined during implementation planning after reading the existing orchestration code.

Conceptual output:

```text
ExecutionDecision
- mode: WORKFLOW | AGENT
- reason_code
```

No probabilistic scoring system is required for V1.

## 6.1 Workflow cases

Remain on the existing path when:

* a known retrieval strategy can answer the request directly;
* all required parameters are already present;
* retrieval steps are independent or statically ordered;
* no later action needs to depend on an earlier observation.

Examples:

```text
"What are the parameters of product X?"
"Are device A and hub B compatible?"
"What does error code E101 mean?"
```

These should continue through the existing:

```text
QueryAnalyzer
→ RetrievalExecutor
→ EvidenceAnswerer
```

path.

## 6.2 Agent cases

Route to Agent when at least one meaningful task transition depends on runtime observation.

Example structure:

```text
Goal
 ↓
lookup A
 ↓
Observation A
 ↓
choose B or C depending on Observation A
 ↓
possibly request missing information
 ↓
retrieve additional evidence
 ↓
verify
```

Agent routing may also apply when:

* a required field cannot be known until a previous lookup has completed;
* missing information is discovered during task execution;
* a retriever failure changes the next strategy;
* an intermediate result must be validated before continuing.

## 6.3 Conservative routing

When the router cannot establish that runtime-dependent reasoning is necessary, V1 SHALL prefer `WORKFLOW`.

The Agent is therefore opt-in by task structure rather than the default execution engine.

## 6.4 Eval override

The Eval Harness SHALL support:

```text
execution_mode = workflow
execution_mode = agent
execution_mode = auto
```

`workflow` and `agent` forcibly select an execution implementation.

`auto` tests the real routing boundary.

This separation is required so that:

* Agent vs Workflow capability can be compared independently of routing quality;
* routing failures do not contaminate the primary capability comparison.

---

# 7. Proposed Architecture

```text
                    React + Vite
                         │
                         ▼
                    Go Gateway
                         │
                         ▼
                    POST /api/chat
                         │
                         ▼
                     FastAPI
                         │
                         ▼
                  ChatOrchestrator
                         │
                         ▼
                  ExecutionRouter
                   /             \
                  /               \
          WORKFLOW                 AGENT
             │                       │
             ▼                       ▼
 Existing Retrieval Path       AgentTaskService
 QueryAnalyzer                       │
 RetrievalExecutor                   ▼
 EvidenceAnswerer              PiRuntimePort
                                     │
                                     ▼
                              Pi Runtime Adapter
                                     │
                               Pi Agent Session
                                     │
                           Model ↔ Tool Loop
                                     │
                                     ▼
                              Agent Tool Adapter
                                     │
                 ┌───────────────────┼─────────────────┐
                 ▼                   ▼                 ▼
             EXACT             GRAPH / Neo4j     KNOWLEDGE SEARCH
                 │                   │                 │
                 └──── existing Python retrieval ─────┘
                                     │
                                     ▼
                                 Evidence
                                     │
                                     ▼
                                Verification
                                     │
                                     ▼
                                Final Answer
```

The Agent path is an extension of the current request pipeline.

It does not introduce a second customer-service backend.

---

# 8. Pi Runtime / FastAPI Responsibility Boundary

Pi owns the **generic Agent mechanics**.

The application owns the **business semantics**.

## Pi Runtime owns

```text
Model invocation
Tool-call loop
Tool result delivery
Observation → continuation
AgentSession lifecycle
Agent runtime events
```

Current Pi SDK documentation exposes `createAgentSession()` and `AgentSession`, while the underlying `Agent` from `pi-agent-core` owns the model/tool interaction state.

## FastAPI/application owns

```text
Workflow vs Agent routing
Task identity
Task State
Tool definitions
Tool authorization
Tool implementation
Evidence representation
Retry policy
Fallback policy
Verification
Trace persistence
Failure Attribution
Evaluation
Regression cases
Final API response
```

Pi SHALL NOT become the source of truth for application state.

## 8.1 Language boundary

Pi's SDK is Node.js/TypeScript based.

Therefore V1 SHOULD introduce a **thin Pi Runtime Adapter process**, rather than porting the Python backend to TypeScript.

Conceptually:

```text
FastAPI
   ↓
PiRuntimeClient
   ↓
thin Node/TS Pi Runtime Adapter
   ↓
Pi AgentSession
```

The adapter SHALL contain minimal business logic.

Its responsibilities are limited to:

* construct an Agent session;
* register the approved Tool contracts;
* execute the Pi loop;
* emit normalized runtime events;
* return the Agent's structured completion result.

Business retrieval SHALL remain in Python.

Exact process transport and module paths SHALL be confirmed during implementation planning.

## 8.2 Pi session persistence

V1 SHALL NOT use Pi session files as application persistence.

Pi sessions may be in-memory.

Persistent application state belongs to the existing backend.

For a task resumed after a user clarification, the Agent context SHALL be reconstructed from persisted `TaskState`.

This prevents two competing persistence models:

```text
Pi Session State
vs
Application Task State
```

The application Task State is authoritative.

---

# 9. Agent Task State

Agent tasks require explicit execution state because conversation persistence alone is insufficient.

The existing repository stores conversations/messages, but does not currently maintain execution state or resume/checkpoint semantics.

Minimum conceptual schema:

```text
TaskState
{
  task_id
  conversation_id

  objective

  known_facts[]
  missing_information[]

  completed_steps[]

  tool_observations[]

  evidence_refs[]

  retry_info

  status
}
```

Allowed task status values:

```text
RUNNING
WAITING_FOR_USER
VERIFYING
SUCCEEDED
FAILED
```

Optional implementation-level states such as `CANCELLED` may be added only if required by existing request handling.

## 9.1 State rules

Task State SHALL:

* represent only the active task;
* not represent a user profile;
* not become semantic memory;
* not automatically import full historical conversations;
* contain normalized facts required to continue the current task.

V1 SHALL support at most **one active Agent task per conversation**.

This intentionally avoids multi-task scheduling.

## 9.2 Multi-turn continuation

If information is missing:

```text
Agent run
→ WAITING_FOR_USER
→ assistant asks explicit clarification
→ response returned through existing /api/chat
```

On the next user message:

```text
/api/chat
→ detect active WAITING_FOR_USER task
→ merge supplied information into TaskState
→ resume Agent execution
```

The message SHALL not be treated as a new unrelated Agent routing decision.

---

# 10. Tool Contract

Agent Tools SHALL expose business-level capabilities.

They SHALL NOT expose infrastructure primitives.

Good:

```text
exact_lookup
compatibility_lookup
knowledge_search
```

Bad:

```text
execute_sql
execute_cypher
query_pgvector
redis_get
```

The current retrievers/services remain the implementation underneath these Tools.

This reuses the existing retrieval layer instead of creating duplicate TypeScript database access.

## 10.1 Generic ToolResult

Conceptual contract:

```text
ToolResult
{
  status:
    OK
    NOT_FOUND
    PARTIAL
    ERROR

  data

  evidence[]

  retryable

  error_code?

  latency_ms
}
```

`NOT_FOUND` is a valid observation.

It SHALL NOT automatically be treated as an infrastructure error.

## 10.2 Evidence

Every Tool capable of producing factual business information SHALL return evidence in the application's existing Evidence representation or a thin compatible extension of it.

The Agent does not manufacture evidence.

The Agent may only reference evidence originating from Tool observations.

## 10.3 Permissions

V1 Tools are read-only.

No generic Tool permission platform is required.

The effective policy is:

```text
Agent Runtime
→ fixed allow-list of approved read-only Tools
```

Existing JWT user boundary and conversation ownership remain enforced at the FastAPI layer. The current repository already provides those API-level boundaries.

---

# 11. Runtime Execution Lifecycle

## New request

```text
1. Authenticate user
2. Verify conversation ownership
3. Check whether conversation has resumable Agent task
4. If not, make Workflow/Agent boundary decision
```

### Workflow route

```text
5A. Execute existing retrieval path
6A. Produce EvidenceAnswerer result
7A. Persist normal messages
8A. Return response
```

### Agent route

```text
5B. Create AgentRun + initial TaskState
6B. Build Pi AgentSession
7B. Supply:
      - objective
      - current TaskState
      - allowed Tool contracts
      - completion protocol

8B. Model chooses next action

9B. If Tool Call:
      validate args
      execute existing Python capability
      record observation
      update TaskState
      append Trace event
      return observation to Pi
      continue loop

10B. Repeat until:
      - clarification required
      - candidate final answer produced
      - hard failure
      - execution limits reached

11B. Run Verification

12B. Persist final state + trace

13B. Persist assistant message

14B. Return through existing chat API
```

---

# 12. Execution Limits

The Agent SHALL be bounded.

Initial defaults:

```text
max tool calls per execution turn: 6
max same transient tool retry: 1
max verification repair attempt: 1
```

These values SHALL be configuration rather than scattered constants.

They may be adjusted during Eval.

They SHALL NOT become a general workflow engine.

---

# 13. Retry / Fallback Behaviour

Retry behaviour must distinguish different failure types.

## 13.1 NOT_FOUND

```text
Tool → NOT_FOUND
```

This is an Observation.

The Agent decides whether:

* another Tool is appropriate;
* user information is missing;
* the task cannot continue.

No automatic identical retry.

## 13.2 Retryable infrastructure error

Example:

```text
temporary retrieval failure
model provider transient failure
transport failure
```

Policy:

```text
retry once
```

Trace the retry.

If it fails again, expose the failure to the Runtime so the Agent can select a different permitted strategy where appropriate.

## 13.3 Existing retrieval fallback

Existing retrieval-specific fallbacks SHALL remain inside the existing retrieval layer.

For example, the Snapshot already documents hybrid/vector degradation behaviour and existing LLM/retrieval fallbacks.

The Agent SHALL NOT reimplement that logic.

## 13.4 Agent Runtime failure

If Pi/model execution becomes unavailable:

* preserve accumulated evidence;
* do not mark the task successful;
* use existing safe answer/follow-up mechanisms where useful;
* mark the Agent Run as failed or incomplete.

Do not silently convert an incomplete dynamic task into a successful Workflow result.

---

# 14. Verification

The Agent SHALL NOT be able to declare application-level success directly.

Conceptually:

```text
Agent proposes Final Answer
        ↓
Verifier
        ↓
ACCEPT / REJECT
```

Only the verifier/application sets:

```text
TaskState.status = SUCCEEDED
```

## 14.1 V1 deterministic checks

At minimum:

### Evidence validity

Every referenced evidence identifier must exist in the current Agent Run.

### Missing-information consistency

A task cannot be successful when required `missing_information` remains unresolved.

### Tool failure consistency

A task cannot be successful when a required step ended in an unresolved Tool error.

### Completion schema

Agent output must conform to the expected structured finalization format.

### Evidence presence

Evidence-required factual answers cannot complete successfully with zero valid evidence.

## 14.2 Verification repair

On deterministic verification failure:

```text
Verifier violations
      ↓
returned to Agent
      ↓
one repair attempt
```

If verification fails again:

```text
FAILED / safe fallback
```

rather than an unverified answer.

## 14.3 No hidden reasoning requirement

Verification and Trace SHALL NOT depend on storing private chain-of-thought.

`Model Decision` means externally observable decisions such as:

```text
CALL_TOOL
ASK_USER
FINALIZE
```

not hidden reasoning text.

---

# 15. Trace / Observability Model

The goal is not production observability.

The goal is:

> Given a bad answer, reconstruct what the Agent did and why the system reached that outcome.

Each Agent Run receives a stable:

```text
run_id
```

and retains the existing HTTP `request_id` where applicable.

Minimum event types:

```text
RUN_STARTED

BOUNDARY_DECISION

MODEL_DECISION

TOOL_CALL
TOOL_RESULT

STATE_TRANSITION

RETRY

VERIFICATION_RESULT

RUN_COMPLETED

EVAL_RESULT
```

Each event SHOULD contain:

```text
sequence_number
timestamp
run_id
request_id

event_type

tool_name?
tool_args?
observation_summary?
evidence_refs?

error_code?
latency_ms?

task_status_before?
task_status_after?
```

Runtime metadata SHOULD record:

```text
model identifier
Pi package/runtime version
prompt version
tool schema version
```

when available.

This directly addresses the current Snapshot limitation where request-level observability exists but a failed answer cannot be reconstructed from existing telemetry alone.

No external tracing platform is required.

---

# 16. Failure Attribution

Failure Attribution operates **after execution**, primarily for Eval and badcase debugging.

V1 taxonomy:

```text
TASK_UNDERSTANDING
NEXT_ACTION
WRONG_TOOL
WRONG_TOOL_ARGS
TOOL_OR_RETRIEVAL
STATE_LOSS
VERIFICATION
FINAL_ANSWER
UNATTRIBUTED
```

## 16.1 Attribution principle

Do not force every failure into an automatically inferred category.

Use deterministic attribution where evidence is clear.

Examples:

```text
Tool returned infrastructure error
→ TOOL_OR_RETRIEVAL
```

```text
schema-valid tool exists,
Agent calls unrelated capability
→ WRONG_TOOL
```

```text
required fact exists in TaskState before resume
but disappears afterward
→ STATE_LOSS
```

Cases requiring semantic judgement may remain:

```text
UNATTRIBUTED
```

until manually reviewed or optionally classified with an LLM-assisted diagnostic.

The LLM is not the authoritative failure source.

---

# 17. Eval Harness

Evaluation is a first-class feature of this iteration.

V1 SHALL implement a repository-local Eval Harness.

No Eval platform is required.

The harness must:

```text
load versioned Eval cases
→ prepare fixtures
→ select execution mode
→ execute case
→ capture trace
→ run deterministic graders
→ optionally invoke limited LLM judge
→ classify failure
→ output case result
→ aggregate metrics
```

## The existing evaluation code is currently limited to routing-oriented metrics/golden data and is not an Agent evaluation system, so it should be extended or reused where appropriate rather than described as already providing this harness.

# 18. Seed Eval Set

Target:

```text
20–30 cases
```

Recommended V1:

```text
24 cases
```

Approximate distribution:

| Slice                    |  Cases |
| ------------------------ | -----: |
| Simple deterministic     |      4 |
| Boundary                 |      4 |
| Complex dynamic path     |      6 |
| Missing information      |      4 |
| Tool / retrieval failure |      3 |
| Verification             |      3 |
| **Total**                | **24** |

Exact business questions SHALL be created from repository fixtures/data verified during implementation planning.

Do not invent production history.

Dataset description:

> V1 uses a manually designed seed evaluation set based on documented business and architectural boundaries. Runtime-discovered failures are subsequently promoted into the regression set.

---

# 19. Eval Case Structure

Conceptual format:

```text
EvalCase
{
  id
  version

  slice
  complex_task: true | false

  initial_user_message

  expected_boundary:
    WORKFLOW | AGENT

  scripted_user_inputs?

  required_outcomes[]

  required_capabilities[]
  forbidden_capabilities[]

  expected_missing_fields?

  failure_injection?

  max_tool_calls?

  judge_config?
}
```

## 19.1 Avoid brittle path assertions

The Eval case SHOULD generally specify:

```text
required capability
```

rather than:

```text
exact Tool sequence
```

unless exact ordering is part of the behaviour under test.

The point of the Agent experiment is to permit runtime-dependent path selection.

Over-specifying a fixed Tool sequence would reproduce the Workflow inside the Eval.

---

# 20. Multi-turn Eval Cases

Missing-information cases must remain automatically executable.

Example conceptual case:

```text
initial message
→ Agent requests device_model
→ Eval Harness supplies scripted device_model
→ Agent resumes
```

Therefore the Agent clarification output SHOULD expose structured information such as:

```text
requested_fields[]
```

in addition to user-facing text.

This allows deterministic continuation without judging arbitrary natural-language questions.

---

# 21. Failure Injection

Tool failure cases require deterministic failure injection.

Eval-only configuration may specify:

```text
first call to capability X
→ injected retryable error
```

or:

```text
capability Y
→ NOT_FOUND
```

The injector SHALL live at the Tool adapter/harness boundary and SHALL NOT affect normal requests.

This allows the same failure scenario to be reproduced across:

```text
Workflow
Agent
Regression
```

without depending on real infrastructure randomly failing.

---

# 22. Grading Layers

## 22.1 Outcome grading

Primary layer.

Deterministic where possible.

Examples:

```text
required fact returned
correct structured status
required clarification occurred
unsupported success did not occur
```

## 22.2 Process grading

Checks the execution process without requiring one exact plan.

Examples:

```text
required capability was eventually used
forbidden Tool was not used
retry limit respected
verification executed
maximum step count respected
```

## 22.3 Boundary grading

For `auto` mode:

```text
actual execution mode
vs
expected execution mode
```

## 22.4 LLM-as-a-Judge

Allowed only for semantic properties difficult to determine reliably in code.

Example:

```text
Does the final answer cover all user-requested aspects?
```

It SHALL NOT be the universal pass/fail judge.

Deterministic graders remain authoritative wherever feasible.

---

# 23. Workflow Baseline Comparison

Every relevant Eval case SHALL be runnable in:

```text
workflow
agent
```

with:

* the same fixture;
* the same user inputs;
* the same Tool/retrieval data;
* the same injected failures;
* the same deterministic outcome grader.

This prevents unfair comparison.

`auto` is evaluated separately.

The experiment therefore produces:

```text
Case
       ├─ Workflow result
       ├─ Agent result
       └─ Auto-routing result
```

---

# 24. Main Metrics

## 24.1 Primary metric

### Complex Task Success Rate

```text
CTSR =
successful complex cases
/
all complex cases
```

A success requires all mandatory deterministic Outcome checks to pass.

Report separately:

```text
Workflow CTSR
Agent CTSR
```

The project may claim that Agent execution improved complex-task performance only if the predeclared Agent complex slice produces a higher CTSR than the Workflow baseline.

If it does not, the experiment has not demonstrated that hypothesis.

## 24.2 Boundary metrics

Report:

```text
Boundary Accuracy
```

and specifically:

```text
Simple Over-Agentization Rate
=
simple cases routed to Agent
/
simple deterministic cases
```

Simple cases exist specifically to detect unnecessary Agent usage.

## 24.3 Diagnostic metrics

Useful but secondary:

```text
success rate by slice
failure count by attribution category
verification rejection count
retry count
tool-call count
```

Latency MAY be reported from Trace but is not a primary success metric.

Token/cost dashboards are not required for V1.

---

# 25. Regression Loop

The failure loop is:

```text
Eval / real demo run
      ↓
Trace
      ↓
Failure Attribution
      ↓
identify cause
      ↓
fix:
Prompt
Runtime
Tool Adapter
Verification
Routing Rule
      ↓
create Regression Case
      ↓
run entire Eval suite
```

A badcase SHALL not be considered fixed merely because one manual rerun succeeds.

It must become a reproducible regression case when practical.

Regression cases SHALL record their origin:

```text
seed
runtime_badcase
manual_regression
```

---

# 26. Data Model Changes

Exact ORM/migration implementation SHALL be determined from the real repository during implementation planning.

Minimum persistence requirement:

## `agent_runs`

Conceptually:

```text
id
conversation_id
user_id
request_id

objective

execution_mode
status

task_state JSONB

final_answer?

failure_category?

started_at
completed_at?
```

## `agent_trace_events`

Conceptually:

```text
id
run_id
sequence_number
event_type

payload JSONB

latency_ms?
error_code?

created_at
```

Separate tables are preferred to embedding an unbounded event array inside one row because individual trace events need ordered inspection and failure analysis.

No separate Memory tables are required.

## Eval storage

Seed/Regression Eval cases SHOULD remain version-controlled files in the repository.

Eval results MAY be written as JSON/JSONL artifacts.

A database-backed Eval platform is unnecessary.

---

# 27. API / Component Boundaries

The existing public chat entry point SHALL remain the user-facing entry.

Do not create a second public Agent chat API unless implementation constraints prove it necessary.

Existing response structure may be minimally extended with optional fields:

```text
execution_mode

agent_run_id?

task_status?

needs_user_input?

requested_fields?
```

Exact Pydantic model changes SHALL be based on the current source.

The existing React frontend should need only minimal changes required to display ordinary responses/clarification questions.

No Agent debugging UI is required.

---

# 28. Component Responsibilities

## ChatOrchestrator

Owns:

```text
request orchestration
Workflow/Agent dispatch integration
message persistence integration
```

Do not duplicate existing ownership checks.

## ExecutionRouter

Owns:

```text
WORKFLOW vs AGENT decision
reason code
```

Does not execute Tools.

## AgentTaskService

Owns:

```text
TaskState lifecycle
start/resume
execution limits
verification handoff
completion status
```

## PiRuntimeClient / Pi Adapter

Owns:

```text
Pi invocation
AgentSession
runtime event normalization
Tool-call transport
```

Does not own business truth.

## Agent Tool Adapter

Owns:

```text
Tool schema validation
existing retrieval/service invocation
ToolResult normalization
Evidence propagation
```

## Verifier

Owns:

```text
application-level completion checks
accept/reject
```

## Trace Recorder

Owns:

```text
ordered execution events
```

## Eval Harness

Owns:

```text
case execution
fault injection
grading
comparison
result aggregation
```

## Failure Attribution

Owns:

```text
failure category assignment from trace + grader result
```

---

# 29. Testing Strategy

## 29.1 Existing regression

Existing deterministic behaviour SHALL remain covered.

The Snapshot's verified CI-compatible backend test command currently reports:

```text
204 passed, 21 skipped
```

while the default full suite encounters Neo4j connection errors when local Neo4j is unavailable.

The new work SHALL not redefine these infrastructure failures as Agent failures.

## 29.2 Unit tests

Required for:

```text
ExecutionRouter

TaskState transitions

Tool input validation

ToolResult normalization

retry policy

execution limits

verification rules

Trace event ordering

deterministic Eval graders

Failure Attribution rules
```

## 29.3 Integration tests

Cover:

```text
FastAPI → AgentTaskService

AgentTaskService → fake PiRuntime

Pi runtime → fake Tool adapter

Tool adapter → existing retrieval services

WAITING_FOR_USER → resume

verification rejection → repair

retryable Tool error → retry

Trace reconstruction
```

## 29.4 Pi smoke test

At least one integration/smoke test SHALL exercise the real Pi runtime interface rather than exclusively mocking it.

It should prove:

```text
prompt
→ Tool Call
→ Tool Result
→ continuation
→ structured completion
```

It does not need to exercise the entire infrastructure stack.

## 29.5 Eval tests

CI or an explicitly documented Eval command SHALL be able to run:

```text
seed set
→ Workflow mode
→ Agent mode
→ Auto mode
→ result report
```

Cases requiring external model credentials may be kept outside the deterministic unit-test lane, but the execution command and expected dependencies must be explicit.

---

# 30. Acceptance Criteria

The iteration is complete when all of the following are true.

### Existing Workflow preserved

Simple deterministic cases continue through the current deterministic retrieval path.

### Controlled Agent path exists

A complex case can execute:

```text
Model
→ Tool
→ Observation
→ Model
→ another action
```

using Pi.

### Dynamic path demonstrated

At least one Eval case requires a later Tool/action to depend on an earlier observation.

### Clarification supported

At least one missing-information case reaches:

```text
WAITING_FOR_USER
```

and resumes successfully from persisted Task State.

### Existing retrieval reused

Agent Tools adapt existing retrieval/services rather than reimplementing PostgreSQL/Neo4j retrieval in the Pi adapter.

### Read-only boundary enforced

No consequential write Tool is exposed.

### Retry/fallback bounded

Retry and fallback behaviour is deterministic, finite, and visible in Trace.

### Verification enforced

The Agent cannot directly set a task to successful.

At least one Eval case proves that an unsupported candidate answer is rejected.

### Replayable trace

For an Agent Run it is possible to reconstruct:

```text
goal
boundary decision
state
model actions
tool calls
arguments
observations
errors
latency
state transitions
verification
final outcome
```

without requiring hidden chain-of-thought.

### Eval Harness exists

20–30 version-controlled seed cases can run automatically.

### Fair baseline exists

The same relevant cases can be executed in forced Workflow and forced Agent modes.

### Primary metric produced

The harness reports:

```text
Workflow Complex Task Success Rate
Agent Complex Task Success Rate
```

### Boundary is measured

Simple deterministic cases measure Agent over-routing.

### Failure Attribution exists

Failed cases can be mapped to the defined small taxonomy or explicitly marked `UNATTRIBUTED`.

### Regression loop demonstrated

At least one failure case can be converted into a regression case and included in later full Eval runs.

---

# 31. Risks / Assumptions

## 31.1 Seed-set overfitting

The initial 20–30 cases are manually designed.

Risk:

```text
prompt/runtime becomes tuned to known cases
```

Mitigation:

* version the seed set;
* do not silently rewrite expected outputs after failures;
* add runtime-discovered failures as separate regression cases;
* clearly describe the dataset as a seed evaluation set rather than production benchmark data.

## 31.2 Pi language boundary

FastAPI is Python while Pi is Node/TypeScript.

Mitigation:

* keep the Pi adapter thin;
* keep business logic and persistence in Python;
* pin the Pi package version used by the experiment;
* record runtime version in Eval metadata.

## 31.3 Model nondeterminism

Identical Agent runs may produce different paths.

Mitigation:

* deterministic graders;
* constrained Tool set;
* bounded Tool calls;
* structured completion protocol;
* reproducible fixtures;
* run-level Trace.

Do not attempt to remove all model nondeterminism.

## 31.4 Infrastructure uncertainty

The Snapshot does not establish that current PostgreSQL, Redis, Neo4j, BGE-M3, Gateway, or model services are all currently running.

Implementation planning SHALL verify required local infrastructure before creating integration tickets that depend on it.

## 31.5 Tool granularity

Tools that are too low-level encourage unnecessary Agent reasoning.

Tools that are too high-level reproduce a fixed Workflow.

Initial Tools should therefore wrap meaningful existing business retrieval capabilities rather than storage primitives or complete end-to-end workflows.

## 31.6 Baseline fairness

Comparing different fixtures or graders would invalidate the experiment.

Workflow and Agent forced modes must share the same:

```text
case
fixture
user inputs
failure injection
grader
```

---

# 32. Implementation-planning Questions to Resolve From Source

These are intentionally **not** guessed in this Spec.

Before implementation tickets are generated, the Coding Agent should inspect the real repository and resolve:

1. Exact insertion point for `ExecutionRouter` around `ChatOrchestrator` / `RetrievalChatService`.
2. Exact existing Evidence type that Agent Tools should return or extend.
3. Which current retriever/service functions form the cleanest adapters for:

   * exact lookup;
   * graph lookup;
   * knowledge search.
4. Current SQLAlchemy/Alembic conventions for `agent_runs` and trace persistence.
5. Current `ChatResponse` schema and safest optional extension points.
6. Exact mechanism for detecting/resuming an active task in a conversation.
7. Exact Pi package version and custom Tool definition API to pin.
8. Transport mechanism between Python and the thin Pi Runtime Adapter.
9. Existing evaluation module pieces that can be reused without preserving obsolete routing-only assumptions.
10. Which repository fixtures/data can support the first 20–30 cases without inventing business records.

These questions belong in implementation planning/ticketing because the Snapshot establishes architecture but does not expose enough source detail to answer them reliably.

---

# 33. Resulting System Boundary

After this iteration the project should conceptually be:

```text
                         ┌─────────────────────┐
                         │ Deterministic Task  │
                         └──────────┬──────────┘
                                    │
                               Workflow
                                    │
                                    ▼
                     Existing Retrieval System


User Request
     │
     ▼
Execution Boundary
     │
     └───────────────────────────────┐
                                     │
                              Dynamic Task
                                     │
                                     ▼
                            Controlled Agent
                                     │
                     ┌───────────────┼───────────────┐
                     ▼               ▼               ▼
                   Tools           State           Trace
                     │               │               │
                     └───────────────┼───────────────┘
                                     ▼
                                Verification
                                     │
                                     ▼
                                  Result


                          Both execution paths
                                  │
                                  ▼
                               Eval Set
                                  │
                 ┌────────────────┴────────────────┐
                 ▼                                 ▼
         Workflow Baseline                   Agent Runtime
                 │                                 │
                 └────────────────┬────────────────┘
                                  ▼
                     Complex Task Success Rate
                                  │
                                  ▼
                       Failure Attribution
                                  │
                                  ▼
                          Regression Set
```

The engineering claim is therefore narrow and testable:

> The existing deterministic retrieval Workflow remains the preferred execution model for predictable customer-service tasks. A constrained Pi-based Agent Runtime is introduced only where runtime observations determine subsequent actions, and its value is evaluated against the existing Workflow using the same reproducible case set.

---

# 34. Spec Self-check

## 1. 有没有为了 Agent 而 Agent？

No.

Deterministic requests remain on the existing Workflow.

The Agent boundary is observation-dependent execution, not request length or intent count.

## 2. 有没有引入不服务核心研究问题的技术？

No.

The only material additions are:

```text
Pi Runtime integration
Task State
Tool adapter
Trace
Retry/fallback policy
Verification
Eval Harness
Failure Attribution
Regression loop
```

Each directly supports the experiment.

No MCP, Multi-Agent, Electron, Memory platform, observability stack, or Eval platform is introduced.

## 3. 有没有与 Current Project Snapshot 冲突？

No known conflict.

The design explicitly reuses the current FastAPI/ChatOrchestrator/retrieval/Evidence path and treats Agent capabilities as new work.

The Snapshot states that the existing runtime has no general Agent Loop or autonomous Tool calling.

## 4. 有没有把未实现能力误写成已有能力？

No.

Agent Runtime, Agent Task State, Agent Tool interface, full Agent Trace, verification layer and Agent Eval Harness are treated as new capabilities.

Existing conversation persistence is not described as Agent Memory.

Existing routing metrics are not described as Agent Eval.

## 5. 是否能够用同一个 Eval Set 公平比较 Workflow 和 Agent？

Yes.

The harness explicitly separates:

```text
forced workflow
forced agent
auto routing
```

while retaining identical fixtures, failures and graders.

## 6. Out of Scope 是否足够严格？

Yes.

This iteration is restricted to:

```text
Complex Task Agent Runtime
+
Trace
+
Eval
+
Failure Analysis
```

Unrelated platform expansion is excluded.

## 7. 是否已经具体到可以生成 implementation tickets？

Yes.

The Spec establishes:

* architectural boundaries;
* component responsibilities;
* runtime lifecycle;
* state model;
* Tool contract;
* retry policy;
* verification contract;
* Trace events;
* Eval schemas;
* grader layers;
* data-model direction;
* test categories;
* acceptance criteria.

The remaining unresolved items are deliberately limited to source-level decisions that require reading the repository during implementation planning.
