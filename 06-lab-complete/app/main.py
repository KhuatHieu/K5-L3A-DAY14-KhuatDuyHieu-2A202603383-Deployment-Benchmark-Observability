"""
Production AI Agent — Part 6 Final Project

Chạy:   uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --workers 2
Kiểm tra: python check_production_ready.py

Thứ tự bảo vệ của POST /ask (lớp rẻ chặn trước, lớp tốn tiền chạy sau cùng):
    X-API-Key (401) → rate limit trong Redis (429) → budget (402) → LLM → ghi chi phí
"""
import json
import logging
import sys
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone

import redis
from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app import redis_client
from app.auth import verify_api_key
from app.config import settings
from app.cost_guard import check_budget, estimate_tokens, record_cost
from app.rate_limiter import check_rate_limit
from utils.mock_llm import ask as llm_ask

APP_VERSION = "1.0.0"


# ─────────────────────────────────────────────────────────
# Structured logging — mỗi dòng log là 1 JSON object
# ─────────────────────────────────────────────────────────
class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "ts": datetime.fromtimestamp(record.created, timezone.utc).isoformat(timespec="milliseconds"),
            "lvl": record.levelname,
            "logger": record.name,
            "event": record.getMessage(),
        }
        entry.update(getattr(record, "fields", {}))
        if record.exc_info:
            entry["exc_type"] = record.exc_info[0].__name__
            entry["exc"] = self.formatException(record.exc_info)
        return json.dumps(entry, ensure_ascii=False, default=str)


def _setup_logging() -> logging.Logger:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]          # thay handler mặc định (format text) bằng JSON
    root.setLevel(settings.log_level)
    return logging.getLogger("agent")


logger = _setup_logging()


def log_event(event: str, level: int = logging.INFO, **fields) -> None:
    logger.log(level, event, extra={"fields": fields})


# ─────────────────────────────────────────────────────────
# Lifecycle — startup / graceful shutdown
# ─────────────────────────────────────────────────────────
_state = {"ready": False, "started_at": time.time()}


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Không ping Redis ở đây: agent phải khởi động được cả khi Redis chưa sẵn sàng.
    # /ready mới là nơi báo "chưa nhận traffic".
    log_event("startup", version=APP_VERSION, env=settings.environment, port=settings.port,
              rate_limit_per_minute=settings.rate_limit_per_minute,
              daily_budget_usd=settings.daily_budget_usd,
              monthly_budget_usd=settings.monthly_budget_usd)
    _state["ready"] = True
    yield
    # Uvicorn chạy phần này khi nhận SIGTERM, SAU KHI đã ngừng nhận kết nối mới và
    # chờ các request đang xử lý trả lời xong → chỉ còn việc dọn dẹp.
    _state["ready"] = False
    log_event("shutdown", uptime_seconds=round(time.time() - _state["started_at"], 1))
    redis_client.close()


app = FastAPI(title="Production AI Agent", version=APP_VERSION, lifespan=lifespan,
              docs_url=None if settings.environment == "production" else "/docs",
              redoc_url=None, openapi_url=None if settings.environment == "production" else "/openapi.json")


@app.middleware("http")
async def access_log(request: Request, call_next):
    """Mỗi request 1 dòng log JSON. Không log body, câu hỏi hay API key."""
    request_id = request.headers.get("x-request-id") or uuid.uuid4().hex[:16]
    started = time.perf_counter()
    status = 500
    try:
        response = await call_next(request)
        status = response.status_code
        response.headers["X-Request-ID"] = request_id
        return response
    finally:
        log_event("request", request_id=request_id, method=request.method, path=request.url.path,
                  status=status, ms=round((time.perf_counter() - started) * 1000, 1))


# ─────────────────────────────────────────────────────────
# Lỗi: trả JSON rõ ràng, không bao giờ lộ stack trace ra client
# ─────────────────────────────────────────────────────────
@app.exception_handler(redis.RedisError)
async def redis_unavailable(request: Request, exc: redis.RedisError):
    log_event("dependency_error", logging.ERROR, path=request.url.path,
              dependency="redis", error=type(exc).__name__)
    return JSONResponse(status_code=503, headers={"Retry-After": "5"},
                        content={"detail": "Service temporarily unavailable, please retry"})


@app.exception_handler(Exception)
async def unhandled_error(request: Request, exc: Exception):
    logger.exception("unhandled_error", extra={"fields": {"path": request.url.path}})
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})


# ─────────────────────────────────────────────────────────
# Health / readiness
# ─────────────────────────────────────────────────────────
@app.get("/health")
def health():
    """Liveness: luôn 200 khi process còn chạy — KHÔNG phụ thuộc Redis
    (Redis chết mà restart agent cũng không sửa được gì)."""
    return {
        "status": "ok",
        "version": APP_VERSION,
        "environment": settings.environment,
        "uptime_seconds": round(time.time() - _state["started_at"], 1),
    }


@app.get("/ready")
def ready():
    """Readiness: 200 khi nhận được traffic; 503 khi đang tắt hoặc mất Redis
    (load balancer tạm ngừng gửi request vào instance này)."""
    if not _state["ready"]:
        return JSONResponse(status_code=503, content={"ready": False, "reason": "shutting down"})
    if not redis_client.redis_healthy():
        return JSONResponse(status_code=503, content={"ready": False, "reason": "redis unavailable"})
    return {"ready": True}


# ─────────────────────────────────────────────────────────
# Agent
# ─────────────────────────────────────────────────────────
class AskRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=2000)
    user_id: str | None = Field(default=None, max_length=128)


@app.post("/ask")
def ask(body: AskRequest, api_key: str = Depends(verify_api_key)):
    # `def` (không async): FastAPI chạy trong threadpool nên LLM chậm không chặn
    # event loop — /health vẫn trả lời ngay trong lúc /ask đang chạy.
    limits = check_rate_limit(api_key)
    check_budget()

    answer = llm_ask(body.question)

    input_tokens, output_tokens = estimate_tokens(body.question), estimate_tokens(answer)
    cost = record_cost(input_tokens, output_tokens)
    log_event("llm_call", input_tokens=input_tokens, output_tokens=output_tokens,
              cost_usd=round(cost, 8), question_chars=len(body.question))
    return {
        "question": body.question,
        "answer": answer,
        "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens,
                  "cost_usd": round(cost, 8),
                  "rate_limit_remaining": limits["remaining"]},
    }
