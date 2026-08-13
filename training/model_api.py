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

logger = logging.getLogger(__name__)


class IntentClassifier(Protocol):
    def predict(self, text: str) -> tuple[str, float]: ...


class PredictionRequest(BaseModel):
    text: str = Field(min_length=1, max_length=500)


class PredictionResponse(BaseModel):
    intent: str
    confidence: float
    latency_ms: float


def build_model_classifier() -> ModelIntentClassifier:
    """Build the real classifier from environment-based configuration."""
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


def create_app(
    classifier_factory: Callable[[], IntentClassifier] | None = None,
) -> FastAPI:
    """Create an app whose model is loaded once during service startup."""

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        started_at = time.perf_counter()
        factory = classifier_factory or build_model_classifier
        logger.info("Loading intent classifier")
        application.state.classifier = factory()
        application.state.model_load_seconds = round(
            time.perf_counter() - started_at,
            3,
        )
        logger.info(
            "Intent classifier ready in %.3f seconds",
            application.state.model_load_seconds,
        )
        yield

    application = FastAPI(
        title="Intent Classification Service",
        version="0.1.0",
        lifespan=lifespan,
    )

    @application.get("/health")
    def health() -> dict:
        return {
            "status": "ready",
            "model_loaded": hasattr(application.state, "classifier"),
            "model_load_seconds": application.state.model_load_seconds,
        }

    @application.post("/predict", response_model=PredictionResponse)
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
            "Intent prediction completed intent=%s latency_ms=%.2f",
            intent,
            latency_ms,
        )
        return PredictionResponse(
            intent=intent,
            confidence=confidence,
            latency_ms=latency_ms,
        )

    return application


app = create_app()
