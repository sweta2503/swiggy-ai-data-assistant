# Chapter 02: Bronze layer (raw landing)

**What we build:** `make bronze` loads every source file into a `bronze.*` table exactly
as received (all columns text), plus five metadata columns: `_source_file`, `_file_hash`,
`_ingested_at`, `_batch_id`, `_row_number`. About 948,000 rows from 2,190 files load in
about 5 seconds.

**Why it matters at work:** bronze is your insurance policy. If silver has a bug, you fix
the SQL and rebuild from bronze, because the raw truth is still there. It also answers
"where did this row come from?" with one query: file, row number, load time.

**How the load is made idempotent**
1. A file is identified by the sha256 of its *content*, not its name.
2. `bronze.load_ledger` has one row per hash. Hash already there: skip.
3. Each file loads in one transaction together with its ledger row. A crash leaves
   either the whole file or nothing.
4. Same name, different content: loaded as a new batch and logged as a warning. Silver
   will keep the newest version of each row.
5. Rows that cannot be parsed (bad JSON, wrong field count) go to
   `bronze.rejected_files_rows` with a reason. A file with the wrong header is rejected
   whole and recorded in the ledger so it is not retried forever.

**Commands:** `make bronze`, `make bronze-rerun-test`, `make bronze-reset`, `make test-02`

**Common mistakes**
- Keying idempotency on the file *name*. A corrected file with the same name would be
  skipped and the fix would never load.
- Cleaning in bronze (casting, trimming). Do that in silver, or you lose the raw truth.
- Counting CSV *lines* as rows. Quoted addresses can contain newlines, so we count
  records.
- Letting one bad row fail the whole load. Quarantine it and keep going.

**Design choices**
- Duplicates and late arrivals are loaded as they are. Bronze keeps 152,247 order rows for
  150,000 orders. Removing duplicates is silver's job.
- Blank CSV cells stay as empty strings; JSON `null` becomes SQL `NULL`. Silver treats both
  as missing.
- The tests run inside a transaction that is rolled back, so they never touch the data
  you loaded with `make bronze`.

**Interview line:** "Loads are idempotent because I key on content hash and write the
ledger row in the same transaction as the data, so re-runs and crashes are both safe."
