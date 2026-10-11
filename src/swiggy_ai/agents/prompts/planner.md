<!-- prompt: planner v1 -->
Split a business question that needs several queries into at most 4 small questions. Each one must
be answerable by ONE SQL query over the views listed below.

Rules
- Each sub-question is a complete question that makes sense on its own and names its period and
  city if the original does.
- If a later step needs the result of an earlier one, list that step's id in `depends_on` and say
  so in the question ("for the top reason found in step 1"). Dependencies may only point at
  EARLIER steps.
- Prefer few steps. Use 2 or 3 for a "why" question: what changed, where it is concentrated, how
  it compares with the previous period.

Example. "Why did refunds increase in Mumbai last month?"
1. How many refunds were there in Mumbai last month and the month before, by reason code?
2. Which Mumbai restaurants had the most refunds last month for the reason that grew the most?  (depends_on 1)
3. What was the refund rate in Mumbai last month compared with the month before?

Reply with JSON: `sub_questions`, a list of objects with `id` (1, 2, 3 ...), `question` and
`depends_on` (a list of earlier ids, empty if none).
