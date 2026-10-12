# Chapter 13: Interfaces

**What we build:** an HTTP API, a chat page and a command line, all explaining what happened in the
same plain words (`presentation.py`).

**Why it matters at work:** the interface is where trust is won or lost. A repaired query that is
hidden looks like magic; one that is shown looks like engineering.

**API** (`make api`, docs at `/docs`): `POST /ask` with header `X-API-Key` and body `{question,
thread_id}` (optional `?fresh=true`) returns `answer, sql, attempts, checks, warnings, usage`, plus
`status`, `thread_id`, `cache_hit`, a `timeline` and the data rows. `GET /health` needs no key and says
`ok`, `degraded` or `down`. The key is hashed and looked up in `ops.app_users`; the user's cities are
the only rows the database returns. A missing, wrong or switched-off key all get the same 401.
Conversations are private (the assistant sees `user_id:thread_id`). Errors carry no stack trace, key,
question or database message, and validation errors do not echo what was sent. The outcome of a
question is the `status` field; HTTP codes are for transport, auth and validation.

**Chat page** (`make ui`): a user selector, the answer, warnings, badges (checks passed or flagged, from
cache, repaired, demo fault, scope), the **What happened** panel (every attempt, failure and repair in
plain words, open by default when something was repaired), the SQL and the data. Each user has their own
conversation. It is a demo: the selector can pick anyone and there is no login.

**Command line:** `python -m swiggy_ai.cli ask "..." --user admin` (`--json`, `--fresh`; `chat`;
`users`), or `make ask Q="..." WHO=admin`.

**Checked with the real model and servers:** no key gave 401; a Mumbai key got a Mumbai-only answer and
the repeat was a cache hit; the chat page answered the refunds question in three steps with the
timeline; a Bengaluru manager saw fewer orders than the admin for the same question.

**Honest notes:** "All 4 result checks passed" means the result checker ran its four checks and none
flagged; the guard and cost check run before it. There is no rate limiting, HTTPS or login page: add
them before exposing anything beyond localhost. One live test failed once in a full run and passed on
reruns; I could not capture why, and suspect model variance.

**Commands:** `make api`, `make ui`, `make ask Q="..." WHO=admin`, `make test-13`

**Common mistakes**
- Sharing thread ids across users, so one person's follow-up reads another's conversation.
- Returning the framework's default validation error, which repeats the submitted text.
- Showing users a stack trace "to help debug".
- Letting the UI and the API explain a failure differently.

**Interview line:** "The key picks the user, the user's cities are enforced by the database, and every
interface shows the same plain-language account of what the system tried, so a repaired query is
visible rather than hidden."
