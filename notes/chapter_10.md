# Chapter 10: Evaluation

**One idea:** you do not know how good the assistant is until you measure it against answers you
wrote yourself, with the same questions, every time.

**What was built**
- `eval/golden.yml`: 30 questions across 8 categories (simple 6, joins 6, time 5, multi-step 4,
  ambiguous 2, unsafe 2, trap 3, security 2). Ambiguous ones must get a clarifying question, unsafe
  ones a refusal, the security ones are asked by city-scoped users.
- `eval/reference_sql/`: one hand-written query per expected answer, written against the **gold
  tables** (a second route to the number, independent of the analytics views the assistant reads).
  "Today" is the day after the newest order, the same rule the assistant follows.
- `eval/judge.py`: scores an answer. Numbers allow only rounding (counts must match exactly, small
  rates get a tight band so 0.08 cannot pass for 0.0766); tables match regardless of row or column
  order unless order is the point; refusals and clarifications must also have run no query.
- `eval/run_eval.py`: runs everything as the right user, forces demo fault injection off (and aborts
  the run if an answer reports one), records a crashed question as a failure and carries on.
- `eval/report.py`: `show`, `compare` (regressions and fixes by question), `gate`.
- `eval/thresholds.yml`: **empty on purpose.** The gate exits 2 ("cannot decide") until you set pass
  marks from numbers you have read, and also on partial runs or runs with fault injection.

**Two real runs (30 questions, gpt-5.4-nano for wording and routing, gpt-5.4-mini for SQL)**

| | first run `20261011-161452` | after two fixes `20261011-162144` |
|---|---|---|
| accuracy after healing | 28/30 (93%) | 29/30 (97%) |
| first-attempt accuracy | 93% | 93% |
| clarification / refusal | 2/2, 2/2 | 2/2, 2/2 |
| over-cautious answers | 0 | 0 |
| healed questions | 0 | 2 (1 ended correct) |
| average latency | 4.8 s | 4.9 s |
| tokens (in / out) | 95k / 7.7k | 98k / 7.6k |
| cost | not computed (prices not set) | not computed (prices not set) |

These come from real runs; the result files are gitignored, so rerun `make eval` to reproduce. A
different run can differ a little, because the model is not perfectly repeatable. Thirty questions
is a small sample: one question is 3 points.

**What the first run found (both are real defects, not bad questions)**
1. **J01, "top 5 restaurants".** Postgres sorts NULLs first in a descending sort. A restaurant with
   no delivered orders last month has a NULL revenue, so it came out as number 1 and pushed real
   restaurants out of the top 5. The guard now rewrites `DESC` to `DESC NULLS LAST` (unless the
   query says `NULLS FIRST` by name). Fixed: J01 is right in the second run.
2. **F03, "orders with a beverage".** The model wrote `item_category = 'Beverage'`; the value is
   `Beverages`, so the answer was a confident 0. The result checker now compares equality and `IN`
   literals against the catalog's complete value lists and sends the query back with the real
   values and the closest match. **This did not fix F03.** The repair fired, but the model dodged by
   searching `item_name ILIKE '%beverage%'` and still returned 0. It stays in the report as a
   failure; I did not tune anything for that single question.

**Known limits**
- Restaurant names are not unique (23 names in Bengaluru are shared by two restaurants), so a
  "group by restaurant_name" can merge two restaurants. The reference groups by id. Not yet handled.
- A zero result from a repaired query is accepted without a warning.
- 30 questions cannot show rare failures. Add a question whenever a real failure is found.

**Your steps**
1. Read `eval/results/<latest>.md`.
2. Set the pass marks in `eval/thresholds.yml` a little below a run you consider good (for example
   `min_refusal_accuracy: 1.0`, `min_category_accuracy: {security: 1.0, unsafe: 1.0}`), then run
   `make eval-gate`. It exits 2 until you do.
3. Set `PRICE_*` in `.env` to get a cost per question.

**Commands:** `make eval`, `make eval-check`, `make eval-compare`, `make eval-gate`, `make test-10`

**Common mistakes**
- Writing reference answers by running the assistant's own SQL. That measures consistency, not truth.
- Changing the prompt until one failing question passes. Fix what the failure reveals instead.
- A gate with no marks that always passes.
- Letting demo fault injection stay on during a measurement.

**Interview line:** "I wrote 30 reference answers independently of the system, scored it on every
change, and the first run found a real sorting bug that no unit test had caught."
