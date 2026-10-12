# Chapter 14: Documentation for viewers

**What we build:** the README (what it is, an architecture diagram, prerequisites, a ten-step quick
start, a make-targets table, a folder tour, troubleshooting, a demo script), `docs/architecture.md`
(four mermaid diagrams), `docs/erd.md`, `docs/interview_cheatsheet.md`, and a note for every phase
that fits on one page.

**Why it matters at work:** documentation is read by someone who was not in the room, usually on a bad
day. It is only worth having if it is true, and docs rot the moment the code moves. So these are
tested like code.

**What the documentation tests check** (`make test-14`)
- Every documented Makefile target appears in the README, and every command in the quick start is a
  real target.
- Every relative link and every path in the folder tour exists.
- The README contains no accuracy figure (those come from `make eval`, never from prose).
- The agent-graph diagram has exactly the edges of the compiled LangGraph graph.
- Every column drawn in the ERD exists in the gold schema.
- Every note has the five parts, fits on a page, and the cheatsheet has its twelve answers.
- A first run on an empty database stays fast (statistics are refreshed after each load step).

**The quick start was run from a clean copy**, on a separate database and Python 3.14 (I develop on
3.11), before the README was finished. It found problems that months on my own machine had hidden:
1. `make silver-full` **never finished** on an empty database (over ten minutes). Tables built inside one
   transaction have no planner statistics, so the planner assumed they were empty and chose nested loops;
   my database had old statistics from earlier runs. Fixed by analysing after each load step: the whole
   load now takes about 43 seconds.
2. That fix had a trap. `ANALYZE` writes row counts in place and they **survive a rollback**, so tests that
   ran the pipeline in a transaction left test-sized counts behind and real queries became 15 times
   slower (five tests failed). The pipeline now analyses only when it owns its transaction, with tests.
3. Eight tests had one day's **total revenue typed in as a number**, so they failed for anyone who ran
   `make data` on another day. They now read it from the database.
4. `make report` was missing from `.PHONY` (found by a documentation test).
The full suite then passed on both setups. One Phase 11 test failed once in the clean copy's first run and
never again; I could not explain it.

**Style rules used throughout:** say what is true, including what is not done; no row counts or
accuracy numbers in prose that go stale; one idea per page; every command is copy-pasteable.

**Commands:** `make test-14`, `make ci`

**Common mistakes**
- A quick start nobody has run from scratch.
- Quoting a headline number in the README. Link to the command that produces it.
- Diagrams drawn once and never compared with the code.
- A "limits" section written last, softly, or not at all.

**Interview line:** "I treat docs as part of the build: the make-target table, the quick start, every
link and the architecture diagram are checked by tests, so they fail when the code moves."
