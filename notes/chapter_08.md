# Chapter 08: The multi-agent system

**What we build:** a LangGraph graph that answers a plain-English question from the warehouse, shows
the SQL, repairs its own failed queries, catches silently wrong answers, and refuses what it must.
Built in four tagged increments (`chapter-08a` to `08d`). The graph picture is in
`docs/architecture.md` and is checked against the compiled graph by a test.

**Why it matters at work:** "the model proposes, code disposes". Most of the safety is ordinary,
testable code around a small model.

| Increment | Adds |
|---|---|
| 08a | schema agent, SQL writer, guard, cost check, executor, narrator that proves its numbers |
| 08b | healer and demo-only fault injection |
| 08c | result checker: join fan-out, wall-clock dates, unanchored periods, odd values |
| 08d | router, clarifier, planner, synthesizer, conversation memory |

**Which parts use a model:** router, clarifier, planner, SQL writer, healer, narrator, synthesizer.
The guard, cost check, executor, result checker, schema agent and all routing decisions are code. The
cheap tier does routing and wording; the stronger tier writes and repairs SQL.

**The guard** (`security/sql_guard.py`, `sqlglot`) passes only exactly one SELECT, over documented
views, with no deny-listed function and no comments, and adds a row limit. Its refusals are final:
the healer never sees them (a query that merely fails to parse is an ordinary mistake and is repaired).

**The healer** fixes six ordinary mistake classes with the smallest change, shown the exact database
error and the views' documentation. It stops at `MAX_HEAL_ATTEMPTS` or when it repeats a query.

**The result checker** catches failures that give no error: *join fan-out* (a trusted control query on
`analytics.orders` alone is compared with the answer), wall-clock dates, a period not anchored to
`data_as_of`, misspelt filter values, item lines counted as orders, and odd values as warnings.

**Verified narration:** every number in the written answer is looked up in the result rows; one firm
retry, then plain figures with a warning.

**Demo fault injection** (`DEMO_FAULT_INJECTION=true`) breaks the first query so healing is easy to
show; it is off by default, flagged everywhere, and never on in `make eval`.

**Memory and refusals:** a checkpointer keyed by `thread_id` keeps history ("and in Pune?"). A code
screen refuses requests to change data, reveal personal data or override the rules, with no model.

**Honest limits:** the missing-date-filter check is a heuristic on the SQL; a small model can still
write a wrong query that passes every check (chapter 10 measures this); conversation memory is
in-process and is lost on restart.

**Commands:** `make test-08` (offline, no key needed); live tests need a key.

**Common mistakes**
- Letting the model that wrote the SQL also vouch for it.
- Healing everything: a refusal that gets "fixed" is a hole.
- Treating `CURRENT_DATE` as harmless against data that stops on a fixed day.
- Trusting a narrator without checking its numbers against the rows.

**Interview line:** "The model proposes, deterministic code disposes: a parser-based guard before the
database, a read-only scoped role in it, a control-total check after it, and a narrator whose numbers
are verified against the result."
