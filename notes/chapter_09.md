# Chapter 09: Observability and audit

**What we build:** a record of every question, what ran, what it cost, and a report on how the
assistant is doing. Because an AI answering business questions has to be accountable: who asked
what, which SQL ran, and when it went wrong.

**Two tables** (`sql/ops/30_audit.sql`):

| Table | One row per | Holds |
|---|---|---|
| `ops.assistant_runs` | question | user, city scope, question, route, status, healed?, tokens, latency, cost, result-check findings, warnings, which models, demo fault? |
| `ops.assistant_attempts` | query attempt | the SQL, status (`ok` / `error` / `rejected`), error class, database error text, latency |

Every question is recorded, including refusals, clarifications and model outages, because a
refusal is exactly what an auditor asks about. A run and its attempts are written in one
transaction: all or nothing. The assistant's own database role cannot read either table.

**No personal data in the record.** Everything stored as text goes through one scrubber first:
phone numbers (with `+91`, spaces or dashes), emails, card numbers, Aadhaar- and PAN-style IDs,
and API keys or bearer tokens. That covers the question as typed, the question as understood
after follow-ups, the generated SQL, and the database error text. Logs use the same scrubber.
Business text is left alone: `ORD00000001`, dates, rupee amounts and big SQL numbers all survive
(tests check both directions). Text is cut to 1,000 characters. If the audit write fails, the
answer still comes back, `audit_ok` is False, and the log says `AUDIT WRITE FAILED` with only
the *type* of the error, never its message (which could contain the data).

**Cost.** Tokens are counted per step and per model tier. Cost is tokens x the price you enter in
`.env` (`PRICE_INPUT_PER_1M`, `PRICE_OUTPUT_PER_1M`, in US dollars per million tokens, from the
provider's pricing page). Because the cheap tier and the SQL tier use different models, you may
also set `..._LOW`, `..._MEDIUM`, `..._HIGH` pairs, which override the plain pair. Prices are never
built in or guessed: if one is missing the cost is NULL and the report says
`cost not computed (prices not set)`. A question that never called the model (a refusal) costs
exactly 0.

**`make report`** (last 7 days; `--days N` for another window):
- questions by outcome (answered, asked to clarify, refused, failed) and by route
- **success rate** = answered / (answered + failed); refusals and clarifications are the system
  working, not failing
- **heal rate** = share of answered questions that needed a repair
- top problems: the error classes of query attempts that did not succeed
- guard rejections: queries the safety guard refused to run
- slowest queries, and the speed of whole questions (average, median, 95th percentile)
- tokens and cost, with cost coverage counted only over questions that used the model

**Two things this chapter found by running it for real**
1. The router sent "Revenue by customer segment" to a clarifying question. The cheap model did
   not know that "revenue" is a defined measure. The router is now given the list of defined
   measures and ways to group, plus worked examples. Measured on 23 realistic questions:
   12 of 16 right before, 23 of 23 after. A bigger model scored about the same, so the cheap
   one stays.
2. My first version of the report counted free refusals as "cost computed", which made the cost
   line look finished when it was not. Coverage now counts only questions that used the model.

**Commands:** `make report`, `make test-09`

**Common mistakes**
- Logging the question verbatim "just for debugging". It is the first place a phone number leaks.
- Treating a missing price as zero. Unknown is not free.
- Counting refusals as failures. A report that punishes the guard for working teaches people to
  loosen it.
- Letting an audit outage take answering down, or hiding that it happened.

**Interview line:** "Every request is audited, atomically, with personal data scrubbed before it
touches storage, and the report separates failures from correct refusals so the numbers mean
something."
