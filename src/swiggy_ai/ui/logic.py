"""What the chat page needs from the rest of the project, kept out of the page so it can be tested
and replaced. (`make ui` is a demo: the user selector can pick anyone, there is no login.)"""
from dataclasses import dataclass
from typing import Any

import streamlit as st

from swiggy_ai.agents.service import Answer, Assistant
from swiggy_ai.db import connect
from swiggy_ai.security.scope import AppUser, list_users


@st.cache_resource(show_spinner="Starting the assistant...")
def get_assistant() -> Assistant:
    return Assistant.for_app()


def demo_users() -> list[AppUser]:
    with connect("etl", autocommit=True) as conn:
        return list_users(conn)


@dataclass
class Status:
    data_version: int | None = None
    last_run: str = "never"
    alerts: int = 0
    quality: str = "unknown"


def pipeline_status() -> Status:
    from swiggy_ai.dq.runner import assert_gold_healthy
    from swiggy_ai.ops.cache import get_data_version
    from swiggy_ai.pipeline.schema_drift import open_alerts
    s = Status()
    try:
        with connect("etl", autocommit=True) as conn:
            s.data_version = get_data_version(conn)
            s.alerts = len(open_alerts(conn))
            s.quality = "healthy" if assert_gold_healthy(conn) else "warning"
            row = conn.execute("SELECT status, finished_at FROM ops.pipeline_runs WHERE status"
                               " <> 'running' ORDER BY started_at DESC LIMIT 1").fetchone()
            if row:
                s.last_run = f"{row[0]} at {row[1]:%Y-%m-%d %H:%M}"
    except Exception:  # noqa: BLE001 - the sidebar is decoration; never break the page for it
        pass
    return s


def ask(question: str, user: AppUser, thread: str, fresh: bool) -> Answer:
    return get_assistant().ask(question, user, thread, fresh=fresh)


def as_dict(a: Answer) -> dict[str, Any]:
    from swiggy_ai import presentation
    return presentation.payload(a, "", "")
