# Chapter 10: Evaluation

**What we build:** 30 golden questions (`eval/golden.yml`) across simple, joins, time, multi-step,
ambiguous, unsafe, trap and security categories; a hand-written reference query for each expected
answer (`eval/reference_sql/`, written against the **gold tables**, independent of the views the
assistant reads); a judge, a runner, a report, `compare` and a gate.

**Why it matters at work:** you do not know how good an AI system is until you measure it against
answers you wrote yourself, with the same questions, every time, and can see what each change did.

**How it scores.** Numbers allow only rounding (counts must match exactly; small rates get a tight
band so 0.08 cannot pass for 0.0766). Tables match regardless of row or column order unless order is
the point. Ambiguous questions must get a clarifying question and unsafe ones a refusal, with no query
run. The runner asks as the right scoped user, forces demo fault injection off (and aborts if an answer
reports one), bypasses the answer cache, and records a crashed question as a failure.

**The gate refuses to pass on an empty list.** `eval/thresholds.yml` ships with every pass mark `null`;
`make eval-gate` exits 2 ("cannot decide") until you set marks from a run you have read, and also on a
partial run or one with fault injection.

**Two real runs (30 questions; nano for routing and wording, mini for SQL)**

| | first run | after two fixes |
|---|---|---|
| accuracy after healing | 28/30 | 29/30 |
| first-attempt accuracy | 93% | 93% |
| clarification and refusal | 2/2 and 2/2 | 2/2 and 2/2 |
| average latency | 4.8 s | 4.9 s |

Thirty questions is a small sample (one question is 3 points) and runs vary a little. Rerun
`make eval` for your own numbers; they are saved under `eval/results/` and are not committed.

**What the first run found: real defects, not bad questions**
1. *"Top 5 restaurants"*: Postgres sorts NULLs first on `DESC`, so restaurants with no sales ranked
   first. The guard now rewrites `DESC` to `DESC NULLS LAST`. Fixed.
2. *"Orders with a beverage"*: the model wrote `'Beverage'` for the value `Beverages` and answered 0.
   A new result-checker repair names the real values. **It did not fix this question** (the model
   sidestepped with a text search); it stays as a failure and I did not tune for it.

**Known limits:** restaurant names are not unique (a "group by name" can merge two restaurants); a
zero result from a repaired query is accepted without a warning; 30 questions cannot show rare failures.

**Commands:** `make eval`, `make eval-check`, `make eval-compare`, `make eval-gate`, `make test-10`

**Common mistakes**
- Writing reference answers by running the assistant's own SQL: that measures consistency, not truth.
- Changing the prompt until one failing question passes.
- A gate with no marks that always passes.
- Leaving demo fault injection on during a measurement.

**Interview line:** "I wrote 30 reference answers independently of the system and scored it on every
change; the first run found a real sorting bug that no unit test had caught."
