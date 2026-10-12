# Chapter 09: Observability and audit

**What we build:** a record of every question, what ran, what it cost, and `make report` on how the
assistant is doing.

**Why it matters at work:** an AI answering business questions must be accountable: who asked what,
which SQL ran, and when it went wrong.

| Table (`sql/ops/30_audit.sql`) | One row per | Holds |
|---|---|---|
| `ops.assistant_runs` | question | user, city scope, route, status, healed?, tokens, latency, cost, findings, models, cache hit |
| `ops.assistant_attempts` | query attempt | the SQL, status, error class, error text, latency |

Every question is recorded, including refusals, clarifications and model outages. A run and its
attempts are written in one transaction. The assistant's own role cannot read either table.

**No personal data in the record.** All stored text goes through one scrubber: phone numbers, emails,
card numbers, Aadhaar- and PAN-style IDs, API keys and bearer tokens, in the question, the SQL and the
database error text. Logs use it too. Business text (`ORD00000001`, dates, rupee amounts) survives, and
tests check both directions. If the audit write fails the answer still returns, `audit_ok` is False,
and the log names only the *type* of the error, never its message.

**Cost.** Tokens are counted per step and per model tier. Cost is tokens times the price you enter in
`.env` (`PRICE_INPUT_PER_1M`, `PRICE_OUTPUT_PER_1M`, optional `_LOW/_MEDIUM/_HIGH` pairs). Prices are
never built in or guessed: if one is missing, cost is NULL and the report says `cost not computed
(prices not set)`. A refusal that never called the model costs exactly 0.

**`make report`** (7 days, `--days N`): outcomes and routes; **success rate** = answered / (answered +
failed), so refusals and clarifications count as the system working; heal rate; top error classes;
guard rejections; slowest queries; whole-question speed (cache hits excluded); tokens and cost, counted
only over questions that used the model.

**Found by running it for real:** the router sent "Revenue by customer segment" to a clarifying
question because the cheap model did not know "revenue" is a defined measure. It now gets the list of
defined measures and worked examples. And the first report counted free refusals as "cost computed".

**Commands:** `make report`, `make test-09`

**Common mistakes**
- Logging the question verbatim "just for debugging": the first place a phone number leaks.
- Treating a missing price as zero. Unknown is not free.
- Counting refusals as failures, which teaches people to loosen the guard.
- Letting an audit outage take answering down, or hiding that it happened.

**Interview line:** "Every request is audited, atomically, with personal data scrubbed before it
touches storage, and the report separates failures from correct refusals so the numbers mean
something."
