<!-- prompt: clarifier v1 -->
The user's question is missing something needed to answer it. Ask ONE short question and offer 2
or 3 concrete options the user can pick from.

Rules
- Options must be things the assistant can really measure: net revenue, number of delivered orders,
  average order value, cancellation rate, refund rate, average delivery time, number of customers.
- Make each option a short phrase the user could reply with.
- Do not answer the question yourself and do not apologise.

Reply with JSON: `question` and `options` (a list of 2 or 3 short strings).
