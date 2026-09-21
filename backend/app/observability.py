"""Request correlation and structured HTTP access logging."""

from __future__ import annotations

import json
import logging
import re
import time
import uuid
from contextvars import ContextVar

from fastapi import Request, Response


REQUEST_ID_HEADER = "X-Request-ID"
_REQUEST_ID_RE = re.compile(r"^[0-9a-f]{32}$")
request_id_context: ContextVar[str] = ContextVar("request_id", default="")
logger = logging.getLogger("uvicorn.error")
logger.setLevel(logging.INFO)


def current_request_id() -> str:
    return request_id_context.get()


async def request_observability(request: Request, call_next) -> Response:
    incoming = request.headers.get(REQUEST_ID_HEADER, "")
    request_id = incoming if _REQUEST_ID_RE.fullmatch(incoming) else uuid.uuid4().hex
    token = request_id_context.set(request_id)
    started = time.perf_counter()
    status = 500
    try:
        response = await call_next(request)
        status = response.status_code
        response.headers[REQUEST_ID_HEADER] = request_id
        return response
    finally:
        logger.disabled = False
        logger.info(
            json.dumps(
                {
                    "event": "backend_request",
                    "request_id": request_id,
                    "method": request.method,
                    "path": request.url.path,
                    "status": status,
                    "duration_ms": round((time.perf_counter() - started) * 1000, 3),
                },
                ensure_ascii=False,
                separators=(",", ":"),
            )
        )
        request_id_context.reset(token)
