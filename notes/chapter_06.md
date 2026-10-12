# Chapter 06: Semantic layer

**What we build:** the layer between the business and the SQL: three files in `semantic/`, the
`analytics` views they describe, a validating loader, and a context builder that gives the
assistant only what a question needs.

| File | Holds |
|---|---|
| `metrics.yml` | one SQL definition per metric, plus conventions (IST, Monday weeks, what "today" means) and ready-made period filters |
| `catalog.yml` | every view and column: description, type, allowed values, join hints, "do not" traps |
| `glossary.md` | plain-English terms (GMV, AOV, active customer, fan-out ...) |

**Why it matters at work:** without it, five analysts and one AI write five versions of "revenue".
The semantic layer says it once (`net_revenue = SUM(order_value - discount)`, delivered orders only)
and the assistant is told to use that text verbatim.

**The analytics views** are flat views over gold (`orders`, `order_items`, `refunds`, `customers`,
`restaurants`, `calendar`, `daily_city_sales`, `data_as_of`). City, restaurant and customer segment
are already on the order row, so most questions need no join, and fewer joins means fewer chances to
double count. The only contact detail is `masked_phone` (last four digits).

**"Today" comes from the data.** `analytics.data_as_of.today` is the day after the newest order, so
"last week" gives the same answer whenever you ask and the demo never goes empty. The assistant is
told never to use `CURRENT_DATE`.

**Three safeguards**
1. *Loader validation:* unknown fields, a metric on a missing view, an expression using a column that
   does not exist (the SQL is parsed, not pattern-matched), or `pii: true` are clear errors.
2. *`make catalog-check`:* the catalog must match the live database: undocumented or stale columns,
   wrong types, values missing from `allowed_values`, or PII-looking names not marked `masked` fail.
3. *Tests against independent SQL:* each metric is compared with a separately written query on silver.

**The context builder** has no LLM: it scores metrics and views by name, synonym and column, so
"sales" finds `net_revenue`. If the text is over budget it trims in a fixed order and never trims the
conventions, the best metric, or the best view's "do not" notes.

**Commands:** `make views`, `make catalog-check`, `make test-06`

**Common mistakes**
- Describing the schema once in a prompt and never checking it again.
- Giving the model a revenue idea in words instead of the SQL.
- Dumping every table into every prompt: cost goes up and answers get worse.
- Using `CURRENT_DATE` against data that stops on a fixed day.

**Interview line:** "A semantic layer is the single source of metric definitions and schema
documentation. I validate it on load, test it against the live database, and feed each question only
the slice it needs."
