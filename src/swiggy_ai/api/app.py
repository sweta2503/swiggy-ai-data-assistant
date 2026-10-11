"""The HTTP API.   `make api`  ->  http://127.0.0.1:8000/docs

    POST /ask     header X-API-Key, body {"question": "...", "thread_id": "optional"}
                  query ?fresh=true skips the answer cache
    GET  /health  no key needed; says whether the service is usable, nothing sensitive

The API key picks the user, and the user's cities are the only rows the database will return
(the key is looked up by its hash in ops.app_users). Errors are short and never carry a stack
trace, a key, a question or a database message.
"""
import logging
import threading
import uuid
from collections.abc import Callable
from typing import Annotated, Any

import psycopg
from fastapi import Depends, FastAPI, Header, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from swiggy_ai import presentation
from swiggy_ai.agents.router import MAX_QUESTION_CHARS
from swiggy_ai.agents.service import Assistant
from swiggy_ai.config import get_settings
from swiggy_ai.db import connect
from swiggy_ai.security.scope import AppUser, lookup_user

log = logging.getLogger("api")
THREAD_PATTERN = r"^[A-Za-z0-9_-]{1,64}$"


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=MAX_QUESTION_CHARS)
    thread_id: str | None = Field(default=None, pattern=THREAD_PATTERN,
                                  description="keep it to continue a conversation")


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str, headers: dict | None = None):
        self.status, self.code, self.message, self.headers = status, code, message, headers


def _error(status: int, code: str, message: str, headers: dict | None = None) -> JSONResponse:
    return JSONResponse({"error": code, "message": message}, status_code=status, headers=headers)


def default_user_lookup(api_key: str | None) -> AppUser | None:
    with connect("etl", autocommit=True) as conn:
        return lookup_user(conn, api_key)


def create_app(assistant: Assistant | Any | None = None,
               user_lookup: Callable[[str | None], AppUser | None] = default_user_lookup,
               health_probe: Callable[[], dict] | None = None) -> FastAPI:
    """`assistant` and `user_lookup` can be replaced, which is how the tests run the API without a
    model or a key."""
    app = FastAPI(title="Swiggy AI Data Assistant", version="1.0",
                  description="Ask business questions in plain English. Answers come from the "
                              "warehouse, with the SQL shown.")
    state = {"assistant": assistant}
    lock = threading.Lock()

    def get_assistant() -> Assistant:
        with lock:  # built on first use, so the API starts (and /health works) without a model
            if state["assistant"] is None:
                try:
                    state["assistant"] = Assistant.for_app()
                except Exception as e:  # noqa: BLE001
                    log.error("assistant could not start: %s", type(e).__name__)
                    raise ApiError(503, "assistant_unavailable",
                                   "The assistant is not available right now.") from e
            return state["assistant"]

    def current_user(x_api_key: Annotated[str | None, Header()] = None) -> AppUser:
        try:
            user = user_lookup(x_api_key)
        except psycopg.Error as e:
            log.error("user lookup failed: %s", type(e).__name__)
            raise ApiError(503, "database_unavailable",
                           "The service cannot reach its database right now.") from e
        if user is None:  # the same answer for a missing, wrong or switched-off key
            raise ApiError(401, "invalid_api_key", "Send a valid key in the X-API-Key header.",
                           {"WWW-Authenticate": "ApiKey"})
        return user

    @app.exception_handler(ApiError)
    async def api_error(_: Request, e: ApiError) -> JSONResponse:
        return _error(e.status, e.code, e.message, e.headers)

    @app.exception_handler(RequestValidationError)
    async def bad_request(_: Request, e: RequestValidationError) -> JSONResponse:
        # FastAPI's default repeats the submitted values back; a question may hold personal data
        fields = sorted({str(err["loc"][-1]) for err in e.errors()})
        return _error(422, "invalid_request", f"Check these fields: {', '.join(fields)}.")

    @app.exception_handler(Exception)
    async def unexpected(_: Request, e: Exception) -> JSONResponse:
        ref = uuid.uuid4().hex[:8]
        log.error("unexpected %s (ref %s)", type(e).__name__, ref, exc_info=False)
        return _error(500, "internal_error", f"Something went wrong. Reference: {ref}")

    @app.post("/ask")
    def ask(body: AskRequest, user: Annotated[AppUser, Depends(current_user)],
            fresh: Annotated[bool, Query(description="skip the answer cache")] = False) -> dict:
        thread = body.thread_id or uuid.uuid4().hex
        # Conversations are private to a user: the same thread_id from two users is two threads.
        answer = get_assistant().ask(body.question, user, f"{user.user_id}:{thread}", fresh=fresh)
        return presentation.payload(answer, thread, scope=user.allowed_cities)

    @app.get("/health")
    def health() -> JSONResponse:
        info = (health_probe or default_health)()
        down = info["database"] != "ok"
        info["status"] = "down" if down else (
            "ok" if info["data_quality"] == "healthy" and info["schema_alerts"] == 0
            else "degraded")
        return JSONResponse(info, status_code=503 if down else 200)

    return app


def default_health() -> dict:
    """Facts only: no names of failed checks, files or users."""
    settings = get_settings()
    info: dict[str, Any] = {"database": "down", "model_configured": bool(
        settings.openai_api_key and (settings.openai_model or settings.openai_model_medium)),
        "data_quality": "unknown", "schema_alerts": 0, "data_version": None,
        "last_daily_run": None}
    try:
        from swiggy_ai.dq.runner import assert_gold_healthy
        from swiggy_ai.ops.cache import ensure_cache_tables, get_data_version
        from swiggy_ai.pipeline.schema_drift import open_alerts
        with connect("etl", autocommit=True) as conn:
            info["database"] = "ok"
            ensure_cache_tables(conn)
            info["data_version"] = get_data_version(conn)
            info["data_quality"] = "healthy" if assert_gold_healthy(conn) else "warning"
            try:
                info["schema_alerts"] = len(open_alerts(conn))
            except psycopg.errors.UndefinedTable:
                pass
            row = conn.execute("SELECT status, finished_at FROM ops.pipeline_runs WHERE status"
                               " <> 'running' ORDER BY started_at DESC LIMIT 1").fetchone()
            if row:
                info["last_daily_run"] = {"status": row[0], "finished_at": row[1].isoformat()}
    except psycopg.Error as e:
        log.error("health probe failed: %s", type(e).__name__)
    return info


app = create_app()
