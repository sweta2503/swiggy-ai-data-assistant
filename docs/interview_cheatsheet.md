# Interview cheatsheet

## The two-minute version

"I built a data assistant for a food-delivery company, end to end. Raw order files land in a
**bronze** layer exactly as received; **silver** cleans them (deduplicates, maps messy status
spellings, converts time zones, and quarantines what it cannot parse); **gold** is a star schema
with a slowly-changing customer dimension and a monthly-partitioned orders fact. Personal data
lives in one table the assistant has no access to. Forty-five data-quality checks gate publishing.

On top of that, people ask questions in plain English. A multi-agent graph routes the question,
writes SQL against a documented semantic layer, and runs it as a read-only database role that
can only see eight views and only the cities the user is allowed. A parser-based guard checks the
SQL before it reaches the database; if a query fails, a healer repairs it, with limits; and a
result checker catches answers that run fine but are wrong, like a join that inflates revenue.
Every number in the final answer is verified against the result rows, and the SQL and every
attempt are shown.

I made it testable and honest: thirty golden questions with reference SQL written independently,
a gate that cannot pass until I set pass marks from a real run, an audit trail with personal data
scrubbed, and a schema-drift path that quarantines a changed source file instead of loading it."

## Twelve follow-ups

**1. How do you make loads idempotent?**
A file is identified by the hash of its *content*, not its name. The ledger row and the data go in
one transaction, so a crash leaves all or nothing. Running it twice loads nothing the second time
(`make bronze-rerun-test` checks that). Silver and gold are incremental from a stored high-water
mark and are tested to give the same result as a full rebuild.

**2. What is SCD Type 2, and how did you use it?**
When a customer's city or segment changes, I close the old row and open a new one with validity
dates instead of overwriting. Windows are half-open (`valid_from <= d < valid_to`), so there is
never a gap or an overlap. Each order points at the version valid on the *order date*, so last
year's Delhi orders stay Delhi orders after the customer moves. A partial unique index allows one
current row per customer.

**3. Why keep personal data in a separate table?**
So it can be protected by the absence of access rather than by hoping a filter is right. Dimensions
carry no contact details, the analytics views expose only a masked phone, and the assistant's role
has no grant on `customer_pii`. A test searches every row of every view for any real customer's
name, email, address or phone.

**4. What is join fan-out and how do you catch it?**
Joining orders to order items repeats each order once per item line, so summing an order-level
amount inflates it. On this data the trap overstates revenue by well over double. The result
checker detects the pattern in the SQL, runs a trusted control query on the orders view alone, and
compares totals; if they differ it sends the writer back with both numbers. The catalog also tells
the model not to do it, but I do not rely on that.

**5. Why is the guard code and not the LLM?**
A model can be talked into things, and it grades its own work generously. The guard parses the SQL
(`sqlglot`) and applies fixed rules: exactly one SELECT, only documented views, no deny-listed
functions, no comments, and it adds a row limit. Its refusals are final: the healer never sees
them. Security rules should be reproducible and testable, so they are code, with a test suite that
attacks them.

**6. How did you partition, and why?**
`fact_orders` is range-partitioned by month on the order date, so a one-month query reads one
partition (a test checks the plan). Postgres needs the partition key inside any unique key, so the
primary key is `(order_id, order_date_ist)` and child tables reference that pair. A default
partition catches strays, and a test asserts it stays empty.

**7. What happens when a source changes its schema?**
Each file's columns are fingerprinted and compared with the expected ones. A mismatch means the
file is quarantined with its rows kept, an alert is raised, and nothing reaches silver or gold.
The owner approves a rename in a reviewed config file and the next run loads it. A new column
needs an explicit "ignore for now". Then `make refresh-catalog` updates the catalog so the healer
can recover from the same change in a view.

**8. How do you evaluate an LLM system?**
Thirty questions across simple, joins, time, multi-step, ambiguous, unsafe, trap and security
categories. Each has a reference answer I wrote as SQL against the gold tables, independent of the
views the assistant reads. The judge allows rounding but not wrong numbers (counts must match
exactly). I report first-attempt accuracy and accuracy after healing separately, over-caution, and
cost. My first real run exposed a genuine bug (descending sorts put NULLs first, so "top 5"
included restaurants with no sales), and I fixed the cause, not the question. The gate refuses to
pass until pass marks are set from a run I have read.

**9. How does self-healing avoid becoming a loop, or a hole?**
It only repairs six ordinary mistake classes (syntax, unknown column or table, ambiguous column,
type mismatch, grouping). It has an attempt limit, stops if it proposes a query it already tried,
and never sees guard refusals or permission errors. Every attempt is recorded and shown, so a
repair is visible instead of hidden.

**10. How do you stop one city's manager seeing another's data?**
Row-level security in the database, not the prompt. The session declares its scope and the policy
reads the login, with `FORCE` because the data is reached through views; no declared scope means
no rows. The ability to change the setting from inside a query is revoked, and the key is stored
only as a hash. The honest limit: one login serves all users, so the guard's "single SELECT" rule
is part of what keeps a session from re-declaring its scope.

**11. How do you keep a cache from serving a wrong answer?**
The key is the normalised question, a *data version* that increases whenever a daily run
publishes, and a hash of the city scope. So new data retires everything, and one scope can never
get another's answer. Only the first question of a conversation is cached, only clean answers are
stored (no warnings, no injected faults, nothing that looks personal), and a broken cache never
breaks answering.

**12. What would you change for production, and what does it not do?**
Add per-key rate limiting, HTTPS and real sign-in (the demo page has none); give each city scope its
own database login; replace the in-memory conversation memory with a persistent store; move
scheduling to the platform's orchestrator (a reference Airflow DAG is included); and grow the
evaluation set whenever a real failure is found. It also cannot guarantee that a small model never
writes a wrong query that passes every check, which is why the evaluation exists.
