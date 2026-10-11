"""The chat page.   `make ui`   ->  http://localhost:8501

The main thing on this page is the "What happened" panel under each answer: every attempt, every
failure and every repair in plain words, so you can watch the assistant fix its own mistakes.
"""
import uuid

import streamlit as st

from swiggy_ai import presentation
from swiggy_ai.ui import logic

ICON = {"ok": ":green[✔]", "error": ":red[✘]", "rejected": ":orange[⚠]", "note": ":blue[↻]"}
TONE = {"good": "green", "warn": "orange", "info": "blue", "demo": "violet"}

st.set_page_config(page_title="Swiggy AI Data Assistant", page_icon="🍽️", layout="wide")


def show_answer(a, scope: str) -> None:
    if a.status == "refused":
        st.warning(a.answer)
    elif a.status == "failed":
        st.error(a.answer)
    else:
        st.markdown(a.answer)
    if a.clarification and a.clarification.options:
        st.caption("You could say: " + " · ".join(a.clarification.options))
    for w in a.warnings:
        st.warning(w)
    badges = presentation.badges(a, scope)
    if badges:
        st.markdown(" ".join(f":{TONE[b.tone]}-badge[{b.label}]" for b in badges))
    steps = presentation.timeline(a)
    if steps:
        with st.expander(f"What happened ({len(a.attempts)} attempt"
                         f"{'s' if len(a.attempts) != 1 else ''})", expanded=a.healed
                         or a.fault_injected):
            for line in steps:
                st.markdown(f"{ICON[line.tone]} {line.text}")
    if a.sql:
        with st.expander("SQL used"):
            st.code(a.sql, language="sql")
    for step in a.steps:
        if step.ok and step.rows:
            with st.expander(f"Data: {step.question}"):
                st.dataframe([dict(zip(step.columns, r, strict=False)) for r in step.rows[:50]])
    st.caption(f"{presentation.usage_line(a)} · {a.latency_ms:,} ms")


# ----------------------------------------------------------------------------- sidebar
users = logic.demo_users()
if not users:
    st.error("No users yet. Run `make seed-users` first.")
    st.stop()

with st.sidebar:
    st.title("Swiggy AI Data Assistant")
    st.caption("Demo page: pick who is asking. A real deployment would sign people in; the API "
               "uses keys.")
    by_label = {f"{u.display_name} ({u.allowed_cities})": u for u in users}
    label = st.selectbox("Asking as", list(by_label))
    user = by_label[label]
    fresh = st.checkbox("Skip the answer cache", help="Always ask the model, even for a "
                        "question answered before")
    if st.button("New conversation"):
        st.session_state.pop(f"chat:{user.user_id}", None)
        st.session_state.pop(f"thread:{user.user_id}", None)
    status = logic.pipeline_status()
    st.divider()
    st.caption(f"Data version {status.data_version} · quality {status.quality}")
    st.caption(f"Last daily run: {status.last_run}")
    if status.alerts:
        st.warning(f"{status.alerts} source file(s) waiting in quarantine")

# Every user has their own conversation, so switching user never shows someone else's answers.
chat = st.session_state.setdefault(f"chat:{user.user_id}", [])
thread = st.session_state.setdefault(f"thread:{user.user_id}", uuid.uuid4().hex[:12])

st.header("Ask about orders, revenue, delivery and refunds")
if not chat:
    st.caption("Try: “How many orders did we get last week?” · “Why did refunds increase in "
               "Mumbai last month?” · “Top 5 restaurants by net revenue last month”")
for who, content in chat:
    with st.chat_message(who):
        if who == "user":
            st.markdown(content)
        else:
            show_answer(content, user.allowed_cities)

if question := st.chat_input("Ask a question"):
    chat.append(("user", question))
    with st.chat_message("user"):
        st.markdown(question)
    with st.chat_message("assistant"):
        with st.spinner("Thinking..."):
            answer = logic.ask(question, user, thread, fresh)
        show_answer(answer, user.allowed_cities)
    chat.append(("assistant", answer))
