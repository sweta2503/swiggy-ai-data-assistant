# Chapter 11: Schema drift

**One idea:** the source team will change a column without telling you. The pipeline must notice,
stop that file at the door, tell a human, and carry on with everything else.

**What was built**
- **Fingerprints.** `ops.expected_schemas` holds the expected columns of each file type and a
  fingerprint (sha-256 of the sorted names). Order does not matter, so a reordered header is fine;
  the loader now reads columns by name, not by position.
- **Quarantine.** A file whose columns differ is NOT loaded. Its rows are kept as received in
  `bronze.quarantine_files` / `quarantine_rows`, and one alert goes to `ops.schema_alerts` (missing
  columns, new columns, and a *suggested* rename). Seeing the same file again adds nothing. Other
  files load as normal. Nothing reaches bronze, so silver and gold cannot be corrupted.
- **Owner approval.** `src/swiggy_ai/pipeline/column_aliases.yml` has two sections: `renames`
  (`orders: {order_amount: order_value}`) and `ignore_new_columns` (`orders: [tip_amount]`). After you
  edit it, `make bronze` loads the parked file, maps the renamed column, closes the alert and records
  how. A rename alone is not enough while another column is unexplained.
- **A warning beside answers.** While an alert is open, the assistant says that newer data may be
  missing, because it is waiting in quarantine.
- **`make drift-demo`.** Writes `data/drift/` (the newest orders file re-sent as the next day, with
  `order_value` renamed `order_amount` and a `tip_amount` column), then shows: no approval ->
  quarantined and alerted; rename only -> still parked; rename plus ignore -> loaded. All inside one
  transaction that is rolled back, so your warehouse is unchanged.
- **`make refresh-catalog`.** Compares `catalog.yml` with `information_schema` and edits the file line
  by line (comments and formatting survive). A disappeared-and-appeared column of the same type is a
  *rename* and keeps its description; new columns are added with a `TODO` description and listed;
  stale columns are removed; changed types are updated. New or missing views are reported, never
  invented. It warns when `metrics.yml` still mentions a vanished column and refuses to leave a broken
  file. `--dry-run` shows the plan.
- **The agent test.** A column of the real `analytics.orders` view is renamed for one test (put back in
  a `finally`). An old-style query fails with `undefined_column`. Before the refresh the healer sees
  only the old name and cannot recover. After `refresh-catalog` the healer is shown the new name and
  repairs the query.

**Decisions worth knowing**
- A file is never loaded on a guess. The suggested rename is a hint for you; the approval is a file
  in the repository, so it is reviewed like code.
- Ignoring a new column means its values are NOT kept. Carrying `tip_amount` through needs new bronze,
  silver and gold columns, which is a separate change.
- `ops.expected_schemas` is seeded once and never overwritten by the loader.
- The old behaviour (a bad header was recorded as `rejected_header` and never retried) could not be
  approved later, so it was replaced. Older ledger rows with that status are left as they were.

**Not done yet:** `make daily` (Phase 12) should stop and report when an alert is open; today the
alert shows up in the loader output, in `ops.schema_alerts` and as a warning beside answers.

**Your steps**
1. `make bronze` once (creates the new tables and records the expected schemas).
2. `make drift-demo` and read the output.
3. When a real alert appears: `SELECT * FROM ops.schema_alerts WHERE status = 'open';`, decide, edit
   `column_aliases.yml`, run `make bronze`.
4. After a view changes: `make refresh-catalog`, fill in any `TODO` descriptions, `make catalog-check`.

**Commands:** `make drift-demo`, `make refresh-catalog`, `make test-11`

**Common mistakes**
- Auto-approving renames because the names look similar.
- Loading the file anyway "so we do not lose data" and letting a NULL-filled column flow into silver.
- Reading columns by position, so a reordered header silently loads the wrong data.
- Refreshing the catalog but forgetting the metrics that refer to the old column.

**Interview line:** "Each file's schema is fingerprinted before it loads. A mismatch is quarantined with
an alert and nothing downstream sees it; the owner approves a rename in a reviewed file, and the
catalog refresh lets the self-healing agent recover from the same change."
