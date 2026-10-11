# Chapter 13: Interfaces

**One idea:** three ways in (an API, a chat page, a terminal), one way of explaining what happened.

**What was built**
- **HTTP API** (`make api`, docs at http://127.0.0.1:8000/docs), in `src/swiggy_ai/api/app.py`.
  - `POST /ask`, header `X-API-Key`, body `{"question": "...", "thread_id": "optional"}`, optional
    `?fresh=true` to skip the answer cache. Returns `answer, sql, attempts, checks, warnings, usage`
    plus `status, thread_id, cache_hit, healed, timeline, data, badges, clarification`.
  - `GET /health` needs no key. It says `ok`, `degraded` (a data-quality warning or an open schema
    alert) or `down` (HTTP 503), with counts and no names.
  - The key is hashed and looked up in `ops.app_users`; the user's cities are the only rows the
    database will return. A missing, wrong or switched-off key gets the same 401.
  - Conversations are private: the assistant is given `user_id:thread_id`, so two users who pick the
    same thread id still have two separate threads.
  - Errors are short and carry no stack trace, key, question or database message: validation errors
    do not echo what was sent (a question can contain a phone number); an unexpected error is a 500
    with a short reference id that is also in the log; an unreachable database is a 503.
  - The outcome of a question is the `status` field (`answered`, `clarify`, `refused`, `failed`).
    HTTP codes are only for transport, authentication and validation problems.
- **Chat page** (`make ui`, http://localhost:8501): a user selector (Admin, Bengaluru manager, Mumbai
  manager, read from `ops.app_users`), a chat box, the answer, warnings, badges (all result checks
  passed or which were flagged, from cache, repaired automatically, demo fault, scope), a **What
  happened** panel (the attempts timeline, opened by default when something was repaired), the SQL,
  and the data behind each step. Each user has their own conversation. It is a demo page: the selector
  can pick anyone and there is no login; the API is the one that checks keys.
- **Command line**: `python -m swiggy_ai.cli ask "question" --user admin` (also `--fresh`, `--json`,
  `chat` for a conversation, `users`), or `make ask Q="..." WHO=mumbai_manager`.
- **One explainer for all three** (`src/swiggy_ai/presentation.py`): the timeline reads like
  "Attempt 1 failed: column total_amount does not exist -> The healer rewrote the query -> Attempt 2
  succeeded (83 ms)". The same code feeds the API's JSON, the page and the terminal.
- **Catalog reload.** `Assistant.for_app()` (what all three use) turns on auditing, the cache (if
  `ANSWER_CACHE=true`) and a catalog reload whenever `make daily` publishes a new data version. This
  closes the gap noted in Phase 12.

**Checked with the real model and real servers:** the API refused a request with no key (401); a Mumbai
key got a Mumbai-only answer and the same question again was a cache hit (25 ms); the chat page
answered "Why did refunds increase in Mumbai last month?" in three steps, finding SPOILED_FOOD and
Ratti Corner, with "All 4 result checks passed" and the timeline open; the CLI showed a Bengaluru
manager 781 orders last week where the admin sees 3,526.

**Honest notes**
- "All 4 result checks passed" means the result checker ran its four checks (values, dates, filter
  spellings, join fan-out) and none flagged. The guard and the cost check run before it.
- In the full test run one live test (`test_why_did_refunds_increase_finds_the_planted_cause`) failed
  once; it passed on five reruns in a row. I could not capture why, so I think it is model variance
  (it needs the router to pick multi-step and the answer to mention spoiled food) but have not proved
  that.
- There is no rate limiting. Add one (per key) before exposing the API beyond localhost.
- The demo users still have the placeholder keys from `.env`. Set real ones and run `make seed-users`.
- Streamlit and uvicorn are not for the public internet as they stand: no HTTPS, no login page.

**Commands:** `make api`, `make ui`, `make ask Q="..." WHO=admin`, `make test-13`

**Common mistakes**
- Sharing thread ids across users, so one person's follow-up reads another's conversation.
- Returning the framework's default validation error, which repeats the submitted text.
- Showing the user a stack trace "to help debug".
- Letting the UI and the API explain a failure differently.
- Putting the user selector on a page that faces real users.

**Interview line:** "The key picks the user, the user's cities are enforced by the database, and every
interface shows the same plain-language account of what the system tried, so a repaired query is
visible rather than hidden."
