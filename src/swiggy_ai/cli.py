"""The command line.

    python -m swiggy_ai.cli ask "How many orders did we get last week?" --user admin
    python -m swiggy_ai.cli ask "..." --user mumbai_manager --fresh --json
    python -m swiggy_ai.cli chat --user bengaluru_manager     # a conversation, follow-ups work
    python -m swiggy_ai.cli users

--user is a user id from `users`. This tool runs on a machine that already holds the database
password, so it does not ask for a key; the API always does.
"""
import argparse
import json
import sys
import uuid
from collections.abc import Callable
from typing import Any

from swiggy_ai import presentation
from swiggy_ai.agents.service import Answer, Assistant
from swiggy_ai.db import connect
from swiggy_ai.logging_setup import setup_logging
from swiggy_ai.security.scope import AppUser, get_user, list_users


def render(a: Answer, thread_id: str, scope: str) -> str:
    out = []
    if a.status == "clarify" and a.clarification:
        out += [a.answer or a.clarification.question]
        if a.clarification.options:
            out += ["  options: " + " | ".join(a.clarification.options)]
    else:
        out += [a.answer or "(no answer)"]
    if a.sql:
        out += ["", "SQL used:", *("  " + line for line in a.sql.splitlines())]
    steps = presentation.timeline(a)
    if steps:
        out += ["", "What happened:", *(f"  {i}. {s.text}" for i, s in enumerate(steps, 1))]
    flags = presentation.badges(a, scope)
    if flags:
        out += ["", "  ".join(f"[{b.label}]" for b in flags)]
    for w in a.warnings:
        out.append(f"WARNING: {w}")
    out += ["", f"{presentation.usage_line(a)}; {a.latency_ms} ms; thread {thread_id}"]
    return "\n".join(out)


def _user(user_id: str) -> AppUser | None:
    with connect("etl", autocommit=True) as conn:
        return get_user(conn, user_id)


def _users() -> list[AppUser]:
    with connect("etl", autocommit=True) as conn:
        return list_users(conn)


def main(argv: list[str] | None = None, assistant: Assistant | Any | None = None,
         find_user: Callable[[str], AppUser | None] = _user,
         all_users: Callable[[], list[AppUser]] = _users, out: Callable[[str], None] = print,
         read: Callable[[str], str] = input) -> int:
    p = argparse.ArgumentParser(prog="swiggy_ai.cli", description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    ask = sub.add_parser("ask", help="ask one question")
    ask.add_argument("question")
    chat = sub.add_parser("chat", help="a conversation (type 'exit' to leave)")
    for s in (ask, chat):
        s.add_argument("--user", required=True, help="a user id, see `users`")
    ask.add_argument("--fresh", action="store_true", help="skip the answer cache")
    ask.add_argument("--json", action="store_true", help="print the API's JSON instead of text")
    sub.add_parser("users", help="list the users you can ask as")
    a = p.parse_args(argv)

    if a.cmd == "users":
        for u in all_users():
            out(f"{u.user_id:<20} {u.allowed_cities:<12} {u.display_name}")
        return 0
    user = find_user(a.user)
    if user is None:
        known = ", ".join(u.user_id for u in all_users()) or "none (run `make seed-users`)"
        out(f"unknown user '{a.user}'. Known users: {known}")
        return 2
    setup_logging()
    try:
        bot = assistant or Assistant.for_app()
    except Exception as e:  # noqa: BLE001 - say what is missing, not a traceback
        out(f"the assistant could not start ({type(e).__name__}). "
            "Check OPENAI_API_KEY and the model names in .env, and that `make up` is running.")
        return 3

    if a.cmd == "ask":
        thread = uuid.uuid4().hex[:12]
        answer = bot.ask(a.question, user, thread, fresh=a.fresh)
        out(json.dumps(presentation.payload(answer, thread, user.allowed_cities), indent=2,
                       default=str) if a.json else render(answer, thread, user.allowed_cities))
        return 0 if answer.status in ("answered", "clarify", "refused") else 1

    thread = uuid.uuid4().hex[:12]
    out(f"Asking as {user.display_name} ({user.allowed_cities}). Type 'exit' to leave.")
    while True:
        try:
            q = read("you> ").strip()
        except (EOFError, KeyboardInterrupt):
            return 0
        if q.lower() in ("exit", "quit"):
            return 0
        if q:
            out(render(bot.ask(q, user, thread), thread, user.allowed_cities) + "\n")


if __name__ == "__main__":
    sys.exit(main())
