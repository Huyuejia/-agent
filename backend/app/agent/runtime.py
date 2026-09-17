"""Thin JSONL stdio client for the in-memory Pi Node runtime adapter."""

from __future__ import annotations

import json
import selectors
import subprocess
import time
from pathlib import Path
from typing import Any, Callable, Sequence

from app.agent.domain import (
    AgentCandidate,
    RuntimeResult,
    TaskState,
    ToolResult,
)


PI_RUNTIME_VERSION = "@earendil-works/pi-agent-core@0.85.1"


class PiRuntimeError(RuntimeError):
    pass


class SubprocessPiRuntimeClient:
    runtime_version = PI_RUNTIME_VERSION

    def __init__(
        self,
        *,
        command: Sequence[str],
        provider: str,
        model: str,
        timeout_seconds: float = 60.0,
        scripted_scenario: str | None = None,
    ) -> None:
        self._command = list(command)
        self._provider = provider
        self._model = model
        self._timeout_seconds = timeout_seconds
        self._scripted_scenario = scripted_scenario

    def run(
        self,
        *,
        objective: str,
        task_state: TaskState,
        tool_schemas: dict[str, dict[str, Any]],
        execute_tool: Callable[[str, dict[str, Any]], ToolResult],
        emit_event: Callable[..., None],
    ) -> RuntimeResult:
        adapter_dir = Path(__file__).resolve().parents[3] / "agent-runtime"
        try:
            process = subprocess.Popen(
                self._command,
                cwd=adapter_dir,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                bufsize=1,
            )
        except OSError as error:
            raise PiRuntimeError(f"unable to start Pi runtime: {error}") from error
        if process.stdin is None or process.stdout is None:
            process.kill()
            raise PiRuntimeError("Pi runtime stdio was not created")

        selector = selectors.DefaultSelector()
        selector.register(process.stdout, selectors.EVENT_READ)
        request = {
            "type": "run",
            "objective": objective,
            "taskState": task_state.model_dump(mode="json"),
            "toolSchemas": tool_schemas,
            "provider": self._provider,
            "model": self._model,
            "scriptedScenario": self._scripted_scenario,
        }
        process.stdin.write(json.dumps(request, ensure_ascii=False) + "\n")
        process.stdin.flush()
        deadline = time.monotonic() + self._timeout_seconds
        try:
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise PiRuntimeError("Pi runtime timed out")
                if not selector.select(remaining):
                    raise PiRuntimeError("Pi runtime timed out")
                line = process.stdout.readline()
                if not line:
                    stderr = process.stderr.read() if process.stderr else ""
                    raise PiRuntimeError(
                        f"Pi runtime exited before completion: {stderr[-1000:]}"
                    )
                try:
                    message = json.loads(line)
                except json.JSONDecodeError as error:
                    raise PiRuntimeError("Pi runtime emitted invalid JSONL") from error
                message_type = message.get("type")
                if message_type == "runtime_event":
                    emit_event(
                        message["eventType"],
                        message.get("payload") or {},
                        latency_ms=message.get("latencyMs"),
                        error_code=message.get("errorCode"),
                    )
                elif message_type == "tool_call":
                    result = execute_tool(
                        message.get("toolName", ""),
                        message.get("arguments") or {},
                    )
                    process.stdin.write(
                        json.dumps(
                            {
                                "type": "tool_result",
                                "callId": message.get("callId"),
                                "result": result.model_dump(mode="json"),
                            },
                            ensure_ascii=False,
                        )
                        + "\n"
                    )
                    process.stdin.flush()
                elif message_type == "completed":
                    if message.get("needsUserInput"):
                        return RuntimeResult(
                            clarification_text=message.get("clarificationText"),
                            requested_fields=message.get("requestedFields") or [],
                        )

                    return RuntimeResult(
                        candidate=AgentCandidate.model_validate(message["candidate"])
                    )
                elif message_type == "error":
                    raise PiRuntimeError(message.get("message") or "Pi runtime error")
                else:
                    raise PiRuntimeError(f"unknown Pi runtime message: {message_type}")
        finally:
            selector.close()
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    process.kill()
