# Chapter 07: Security

**What we build:** independent layers between the assistant and the data, so no single mistake (a bad
prompt, a clever SQL string, a forgotten grant) exposes anything. The picture is in
`docs/architecture.md`.

**Why it matters at work:** an AI that writes SQL is an untrusted user with a fast keyboard. The
database must stay safe even if everything above it is fooled.

| Layer | What it stops |
|---|---|
| `assistant_ro` role | reading raw layers or `customer_pii`; any write, DDL, `COPY`, new schema |
| Session defaults | long queries (8 s timeout), idle transactions, leaving the search path, writes |
| Views | personal data in results (only a masked phone exists) |
| Row-level security | seeing another city's rows (fails closed: no scope declared means no rows) |
| `set_config` revoked | widening the city scope from inside a query |

**Least privilege.** `assistant_ro` can `SELECT` exactly the views listed in `semantic/catalog.yml`;
a new view is unreadable until it is documented. It can read gold through views because a view runs
with its owner's rights, which is also why row-level security needs `FORCE`.

**City scoping.** A session declares `SET app.allowed_cities`; the policy reads `session_user` (the
login), not `current_user` (inside a view that is the owner). A role's ceiling in
`ops.role_city_access` caps what it may request. API keys map to users in `ops.app_users` and only
the SHA-256 hash is stored; unknown cities are an error, so a typo can never silently widen a view.

**What the tests try, and fail, to do:** read every raw table; run many kinds of write and DDL
(including `COPY ... TO PROGRAM` and `CREATE EXTENSION dblink`); call `set_config`, `pg_read_file`;
sleep past the timeout; ask for all cities while scoped to one; inject a quote and a `DROP` into the
scope; search every row of every view for any real customer's name, email, address or phone. Then
they weaken the setup in a dozen ways and check `make security-verify` notices each.

**Honest limits**
- One login serves every user, so inside that session the database cannot tell the application's
  `SET` from a malicious one. We close the function route and the guard allows a single `SELECT`, so
  `SET` never reaches the database. For more, give each scope its own login role.
- Row-level security filters rows, not columns; personal data is kept out by the views and by
  `customer_pii` having no grants.

**Commands:** `make security`, `make security-verify`, `make seed-users`, `make security-demo`,
`make test-07`

**Common mistakes**
- Giving the assistant the pipeline's login "to keep it simple".
- Row-level security without `FORCE` when views are involved: it silently does nothing.
- A policy that treats "no scope" as "everything".
- Using `pg_has_role` to detect restricted users: true for every superuser (a test found this).
- Storing the real API key instead of its hash.

**Interview line:** "I layer the controls: a read-only role limited to documented views, session
limits, views with no personal data, and row-level security that fails closed. Each layer is tested
by trying to break it, and a verifier fails the build if any layer is weakened."
