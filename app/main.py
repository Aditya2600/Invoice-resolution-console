from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse

from app.api.routes import router
from app.core import observability
from app.db.pg import init_schema


@asynccontextmanager
async def lifespan(_: FastAPI):
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    init_schema()
    yield


app = FastAPI(
    title="Invoice Resolution Console",
    version="0.1.0",
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(router)


@app.middleware("http")
async def correlate_and_time(request: Request, call_next):
    """Gives every request an id that each log line it causes carries, and counts the outcome."""
    incoming = request.headers.get("X-Request-ID")
    with observability.request_context(incoming) as correlation_id:
        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            observability.count("invoice_http_requests_total", outcome="error")
            observability.log("http", path=request.url.path, outcome="error")
            raise
        duration_ms = round((time.perf_counter() - started) * 1000, 2)
        observability.count(
            "invoice_http_requests_total", outcome="error" if response.status_code >= 500 else "success"
        )
        observability.log(
            "http",
            path=request.url.path,
            outcome="success" if response.status_code < 400 else "error",
            error_code=str(response.status_code) if response.status_code >= 400 else None,
            duration_ms=duration_ms,
        )
        response.headers["X-Request-ID"] = correlation_id
        return response


@app.get("/metrics", response_class=PlainTextResponse, include_in_schema=False)
def metrics() -> str:
    """Prometheus scrape target. Labels are bounded and carry no invoice content — see
    app/core/observability.py. Reports this process only; the worker is scraped separately."""
    return observability.render()

