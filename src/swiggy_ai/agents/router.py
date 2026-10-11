"""Router: decide what kind of question this is, and resolve follow-ups.

Two stages. First a CODE screen catches requests that must never be answered (change data, reveal
personal details, override the rules), so those refusals do not depend on a model behaving. Then a
small model classifies everything else and rewrites follow-ups ("and in Pune?") as complete
questions. The rewritten question is screened again, so a follow-up cannot smuggle anything in.
"""
import re
from collections.abc import Callable

from pydantic import BaseModel

from swiggy_ai.agents.runtime import Runtime
from swiggy_ai.agents.state import AgentState, Route, Turn

MAX_QUESTION_CHARS = 1000

REFUSALS = {
    "changes_data": "I can only read business data, never change it, so I cannot delete, update, "
                    "insert or drop anything.",
    "personal_data": "I cannot share personal details such as customers' phone numbers, emails, "
                     "addresses or names. I can tell you how many customers there are and how "
                     "they break down by city or segment.",
    "override_rules": "I cannot ignore or change the rules I work under.",
    "out_of_scope": "That is outside what I can help with. I answer questions about orders, "
                    "revenue, deliveries, refunds, customers and restaurants.",
    "too_long": f"That message is too long. Please keep questions under {MAX_QUESTION_CHARS} "
                "characters.",
}

_DESTRUCTIVE = re.compile(
    r"^\s*(?:(?:please|kindly|can you|could you|would you|i want to|i need to|go ahead and|"
    r"now)\s+)*(delete|drop|truncate|erase|wipe|destroy|remove|insert|alter|grant|revoke)\b"
    r"|^\s*update\s+\w+.*\bset\b|\b(delete|drop|truncate|erase|wipe) (all|every|the|my|our)\b"
    r"|\b(delete|drop|truncate) (table|view|database|schema|from)\b", re.I)
_PII_FIELD = r"(phone|mobile|e-?mail|address|contact (?:details|info|number)|whatsapp|full name|" \
             r"names?|passwords?|ids? proof|aadhaar)"
_PII_WHO = r"(customers?|users?|riders?|partners?|drivers?|people|persons?|individuals?)"
_PII = re.compile(
    rf"\b{_PII_FIELD}s?\b.{{0,40}}\b{_PII_WHO}\b|\b{_PII_WHO}\b.{{0,40}}\b{_PII_FIELD}s?\b", re.I)
# a question that COUNTS people ("how many customers have a phone number?") is not asking for them
_PII_COUNT = re.compile(
    r"\bhow many\b|^\s*what (percentage|percent|share|proportion|fraction) of\b", re.I)
_OVERRIDE = re.compile(
    r"ignore (all |the |your |any )?(previous|prior|above|earlier)? ?(instructions|rules|prompt)"
    r"|(reveal|show|print|repeat|tell me) (me )?(your|the) (system |hidden |original )?"
    r"(prompt|instructions|rules)|you are now|pretend (to be|you are)|developer mode|jailbreak"
    r"|disable (the )?(guard|safety|security)|bypass", re.I)


def screen_question(question: str) -> tuple[Route, str] | None:
    """Return (route, refusal key) for requests that must be refused, else None."""
    if len(question) > MAX_QUESTION_CHARS:
        return "unsafe", "too_long"
    if _OVERRIDE.search(question):
        return "unsafe", "override_rules"
    if _DESTRUCTIVE.search(question):
        return "unsafe", "changes_data"
    if _PII.search(question) and not _PII_COUNT.search(question):
        return "unsafe", "personal_data"
    return None


class RouteDecision(BaseModel):
    route: Route
    question: str = ""
    reason: str = ""


def format_history(turns: list[Turn]) -> str:
    if not turns:
        return "Earlier conversation: none."
    lines = ["Earlier conversation (oldest first):"]
    for t in turns:
        lines.append(f"- User asked: {t.question}")
        if t.rewritten and t.rewritten != t.question:
            lines.append(f"  understood as: {t.rewritten}")
        if t.answer:
            lines.append(f"  assistant ({t.route}): {t.answer[:300]}")
    return "\n".join(lines)


def make_router(rt: Runtime) -> Callable[[AgentState], dict]:
    def router(state: AgentState) -> dict:
        question = state.question.strip()
        hit = screen_question(question)
        if hit:
            return {"route": hit[0], "working_question": question, "refusal": REFUSALS[hit[1]],
                    "route_reason": f"screened: {hit[1]}"}

        decision, usage = rt.ask(
            state, "router", "router",
            f"{format_history(state.history[-3:])}\n\nNewest message: {question}",
            RouteDecision, tier="low")
        working = (decision.question or question).strip()
        again = screen_question(working)  # the rewrite must pass the screen too
        if again:
            return {"route": again[0], "working_question": working, "usage": usage,
                    "refusal": REFUSALS[again[1]], "route_reason": f"screened: {again[1]}"}
        update = {"route": decision.route, "working_question": working,
                  "route_reason": decision.reason, "usage": usage}
        if decision.route == "unsafe":
            update["refusal"] = f"I cannot help with that. {decision.reason}".strip()
        elif decision.route == "out_of_scope":
            update["refusal"] = REFUSALS["out_of_scope"]
        return update
    return router
