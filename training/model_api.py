"""FastAPI service exposing the reusable intent classifier."""

from __future__ import annotations

import logging
import os
import time
from collections.abc import Callable
from contextlib import asynccontextmanager
from typing import Protocol

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from training.intent_classifier import ModelIntentClassifier
from training.onnx_intent_classifier import OnnxIntentClassifier

logger = logging.getLogger(__name__)


class IntentClassifier(Protocol):
    def predict(self, text: str) -> tuple[str, float]: ...


class PredictionRequest(BaseModel):
    text: str = Field(min_length=1, max_length=500)


class PredictionResponse(BaseModel):
    intent: str
    confidence: float
    latency_ms: float
    model_version: str


class ReadinessResponse(BaseModel):
    status: str
    model_loaded: bool
    model_version: str
    model_load_seconds: float


def build_model_classifier() -> IntentClassifier:
    """Build the real classifier from environment-based configuration."""
    runtime = os.getenv("MODEL_RUNTIME", "pytorch").strip().lower()
    if runtime == "onnx":
        return OnnxIntentClassifier(
            model_path=os.getenv(
                "MODEL_ONNX_PATH",
                "training/runs/qlora-20260627/deployment/onnx-fp16",
            ),
            provider=os.getenv(
                "MODEL_ONNX_PROVIDER",
                "CUDAExecutionProvider",
            ),
        )
    if runtime == "pytorch":
        return ModelIntentClassifier(
            base_model=os.getenv(
                "MODEL_BASE_NAME",
                "Qwen/Qwen2.5-1.5B-Instruct",
            ),
            adapter_path=os.getenv(
                "MODEL_ADAPTER_PATH",
                "training/runs/qlora-20260627/checkpoints/lora-adapter/final",
            ),
        )
    raise ValueError(
        f"Unsupported MODEL_RUNTIME={runtime!r}; expected 'pytorch' or 'onnx'"
    )


def create_app(
    classifier_factory: Callable[[], IntentClassifier] | None = None,
) -> FastAPI:
    """Create an app whose model is loaded once during service startup."""

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        started_at = time.perf_counter()
        factory = classifier_factory or build_model_classifier
        application.state.model_loaded = False
        application.state.model_version = os.getenv(
            "MODEL_VERSION",
            "intent-qwen2.5-1.5b-qlora-v1",
        )
        logger.info(
            "Loading intent classifier model_version=%s",
            application.state.model_version,
        )
        application.state.classifier = factory()
        application.state.model_load_seconds = round(
            time.perf_counter() - started_at,
            3,
        )
        application.state.model_loaded = True
        logger.info(
            "Intent classifier ready model_version=%s load_seconds=%.3f",
            application.state.model_version,
            application.state.model_load_seconds,
        )
        yield

    application = FastAPI(
        title="Intent Classification Service",
        version="0.2.0",
        lifespan=lifespan,
    )

    @application.get("/health")
    def health() -> dict:
        return {"status": "ok"}

    @application.get("/ready", response_model=ReadinessResponse)
    def ready() -> ReadinessResponse:
        if not application.state.model_loaded:
            raise HTTPException(
                status_code=503,
                detail="Model is not ready",
            )

        return ReadinessResponse(
            status="ready",
            model_loaded=True,
            model_version=application.state.model_version,
            model_load_seconds=application.state.model_load_seconds,
        )

    @application.post(
        "/predict",
        response_model=PredictionResponse,
        include_in_schema=False,
    )
    @application.post(
        "/v1/intent/predict",
        response_model=PredictionResponse,
    )
    def predict(payload: PredictionRequest) -> PredictionResponse:
        started_at = time.perf_counter()
        try:
            intent, confidence = application.state.classifier.predict(
                payload.text
            )
        except Exception as exc:
            logger.exception("Intent prediction failed")
            raise HTTPException(
                status_code=503,
                detail="Model inference failed",
            ) from exc

        latency_ms = round((time.perf_counter() - started_at) * 1000, 2)
        logger.info(
            "Intent prediction completed model_version=%s "
            "intent=%s latency_ms=%.2f",
            application.state.model_version,
            intent,
            latency_ms,
        )
        return PredictionResponse(
            intent=intent,
            confidence=confidence,
            latency_ms=latency_ms,
            model_version=application.state.model_version,
        )

    return application


app = create_app()
