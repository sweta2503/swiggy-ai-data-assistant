# Chapter 11: Schema drift

**What we build:** a loader that notices when a source file's columns change, a quarantine and alert
path, an owner-approved mapping file, `make drift-demo`, and `make refresh-catalog`.

**Why it matters at work:** the source team will rename a column without telling you. The pipeline
must stop that file at the door, tell a human, and carry on with everything else, instead of loading
NULLs into silver or failing the whole night's run.

**Fingerprints.** `ops.expected_schemas` holds each file type's expected columns and a fingerprint
(sha-256 of the sorted names). Order does not matter and columns are read by name, so a reordered
header is harmless.

**Quarantine.** A mismatching file is not loaded. Its rows are kept as received in
`bronze.quarantine_files/rows` and one alert goes to `ops.schema_alerts` (missing columns, new columns,
a *suggested* rename). Seeing the file again adds nothing. Other files load normally, and nothing
reaches silver or gold. While an alert is open the assistant warns that the newest data may be missing.

**Approval is a reviewed file.** `src/swiggy_ai/pipeline/column_aliases.yml` has `renames`
(`orders: {order_amount: order_value}`) and `ignore_new_columns` (`orders: [tip_amount]`). After you
edit it, `make bronze` loads the parked file, maps the column, and closes the alert. A rename alone is
not enough while another column is unexplained. Ignoring a column means its values are **not** kept;
carrying it through needs new bronze, silver and gold columns, a separate change.

**`make drift-demo`** writes a later orders batch with `order_value` renamed `order_amount` and a new
`tip_amount`, then shows no approval (quarantined), rename only (still parked), rename plus ignore
(loaded). It runs in one rolled-back transaction, so your warehouse is unchanged.

**`make refresh-catalog`** edits `catalog.yml` line by line (comments survive): a column that vanished
while one of the same type appeared is a *rename* and keeps its description; new columns get a `TODO`
and are listed; stale ones are removed; types are updated. New or missing views are reported, never
invented, and it warns if `metrics.yml` mentions a vanished column.

**The agent test:** a view column is renamed for one test. An old-style query fails with
`undefined_column`; before the refresh the healer cannot recover; after `refresh-catalog` it repairs
the query using the new name.

**Commands:** `make drift-demo`, `make refresh-catalog`, `make test-11`

**Common mistakes**
- Auto-approving renames because the names look similar.
- Loading the file anyway "so we do not lose data".
- Reading columns by position, so a reordered header loads the wrong data.
- Refreshing the catalog but forgetting the metrics that use the old column.

**Interview line:** "Each file's schema is fingerprinted before it loads; a mismatch is quarantined
with an alert, the owner approves a rename in a reviewed file, and the catalog refresh lets the
self-healing agent recover from the same change."
