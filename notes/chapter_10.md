# Chapter 10: Evaluation

**What we build:** 30 golden questions (`eval/golden.yml`) across simple, joins, time, multi-step,
ambiguous, unsafe, trap and security categories; a hand-written reference query for each expected
answer (`eval/reference_sql/`, written against the **gold tables**, independent of the views the
assistant reads); a judge, a runner, a report, `compare` and a gate.

**Why it matters at work:** you do not know how good an AI system is until you measure it against
answers you wrote yourself, every time, and can see what each change did.

**How it scores.** Numbers allow only rounding (counts must match exactly; small rates get a tight
band so 0.08 cannot pass for 0.0766). Tables match in any order unless order is the point. Ambiguous
questions must get a clarifying question and unsafe ones a refusal, with no query run. The runner asks as
the right scoped user, forces demo fault injection off (aborting if one is reported), bypasses the answer
cache, and records a crash as a failure.

**The gate** first shipped empty and refused to pass (exit 2) until marks were set from a run I had read. They
are now in `eval/thresholds.yml`: accuracy after healing at least 0.90; refusal and clarification accuracy 1.0;
no over-cautious answers; floors of 1.0 for `security`, `unsafe` and `ambiguous`. Unset on purpose: first-attempt
accuracy (noisy), latency (machine-dependent) and cost (needs prices). `trap` has no floor yet (it was
left off while F03 failed rather than hide it). The gate passes on the run its marks came from, so its value is
on later runs; it still exits 2 on a partial run or fault injection.

**Real runs (30 questions; nano for routing and wording, mini for SQL):** 28/30, then 29/30 after two fixes, 29/30
again, and 30/30 after the F03 fixes below, with clarification and refusal at 2/2 each time. Thirty questions is
a small sample (one is 3 points) and runs vary a little. Reports are not committed.

**What the runs found: real defects, not bad questions**
1. *"Top 5 restaurants"*: Postgres sorts NULLs first on `DESC`, so restaurants with no sales ranked first.
   The guard now rewrites it to `DESC NULLS LAST`.
2. *"Orders with a beverage"* failed for three stacked reasons. The model wrote `'Beverage'` for `Beverages`
   (answered 0): the result checker now names the real values. It was never shown `order_items`, as nothing
   in the question matched that view: a value only one view can hold now pulls that view in. Then it wrote
   `COUNT(*)` on a table with one row per item line, giving item lines while saying "distinct orders": a new
   check sends it back to `COUNT(DISTINCT order_id)`. F03 then passed 5 of 5 single runs.

**Known limits:** restaurant names are not unique (a "group by name" can merge two restaurants); a zero
result from a repaired query gets no warning.

**Commands:** `make eval`, `make eval-check`, `make eval-compare`, `make eval-gate`, `make test-10`

**Common mistakes**
- Writing reference answers by running the assistant's own SQL: that measures consistency, not truth.
- Changing the prompt until one failing question passes.
- A gate that always passes: no marks, or marks set so loosely nothing can fail.

**Interview line:** "I wrote 30 reference answers independently of the system and scored it on every
change; the first run found a real sorting bug that no unit test had caught."
