# Chapter 06: Semantic layer

**What we build:** the layer between the business and the SQL. Three files in `semantic/`, the
analytics views they describe, a validating loader, and a context builder that gives the
assistant only what a question needs.

| File | Holds |
|---|---|
| `metrics.yml` | 10 metrics, one SQL definition each, plus conventions (IST, Monday weeks, what "today" means) and ready-made period filters |
| `catalog.yml` | 8 views, 77 columns: description, type, allowed values, join hints, "do not" traps |
| `glossary.md` | 20 plain-English terms (GMV, AOV, active customer, fan-out ...) |

**Why it matters at work:** without it, five analysts and one AI write five versions of
"revenue". A semantic layer says it once: `net_revenue = SUM(order_value - discount)` for
delivered orders only. The assistant is told to use that text verbatim.

**The analytics views.** Flat views over gold (`analytics.orders`, `order_items`, `refunds`,
`customers`, `restaurants`, `calendar`, `daily_city_sales`, `data_as_of`). City, restaurant
name and the customer's segment are already on the order row, so most questions need no join,
and fewer joins means fewer chances to double count. There is no personal data: the only
contact detail is `masked_phone` (last four digits). They are created by `make gold` (or
`make views`). Chapter 7 puts roles and row-level security around them.

**"Today" comes from the data.** `analytics.data_as_of.today` is the day after the newest
order. "Last week" and "yesterday" then give the same answer whenever you ask, and the demo
never returns empty results because the calendar moved on. The assistant is told never to use
`CURRENT_DATE`.

**Three safeguards.**
1. *Loader validation.* Unknown field names, a metric on a missing view, an expression using a
   column that does not exist (the SQL is parsed, not pattern-matched), a non-date
   `time_column`, a missing required metric, or `pii: true` are all errors with a clear
   message.
2. *`make catalog-check`.* Compares `catalog.yml` with the live database. A new view or column
   nobody documented fails. A documented column that no longer exists fails. A wrong type
   fails. A value in the data that is not in `allowed_values` fails. A column named like
   personal data (phone, email, address ...) fails unless marked `masked`.
3. *Tests against independent SQL.* Every metric is run against the views and compared with a
   separately written query on silver. The period filters are compared with ranges computed in
   Python, including "last week runs Monday to Sunday".

**The context builder** (`build_context(question, budget_tokens)`). No LLM: it scores metrics
and views by name, synonym and column match, so "sales" finds `net_revenue`, "basket size"
finds `aov`, "signups" finds `new_customers`. It renders conventions, the matching metrics
(exact SQL), the matching views (columns, allowed values, traps), other views as one-liners,
and glossary terms. If the text is over budget it trims in a fixed order: glossary, the
other-views list, extra metrics, then detail of lower-ranked views. It never trims the
conventions, the best metric, or the best view's "do not" notes. Typical questions need
1,900 to 2,000 tokens of the default 2,200 budget; "Best customers?" needs about 900.

**Commands:** `make views`, `make catalog-check`, `make test-06`

**Common mistakes**
- Describing the schema once in a prompt and never checking it again. Columns change; the
  catalog check makes that a failing build instead of a wrong answer.
- Giving the model a revenue idea in words and hoping. Give it the SQL.
- Dumping every table into every prompt. Cost goes up and answers get worse.
- Using `CURRENT_DATE` against data that stops on a fixed day.

**Interview line:** "A semantic layer is the single source of metric definitions and schema
documentation. I validate it on load, test it against the live database, and feed each question
only the slice it needs."
