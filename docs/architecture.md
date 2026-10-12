# Architecture

Four pictures: how data flows, how a question is answered, how access is limited, and how a
request and the daily run fit together. Every box is a real file or table; the agent-graph
picture is checked against the compiled graph by a test, so it cannot drift from the code.

## a) Data flow: sources to answers

```mermaid
flowchart LR
    SRC["Source files<br/>orders.jsonl, payments.csv, customers,<br/>items, refunds, ratings, restaurants"]
    subgraph BRONZE["bronze: exactly as received"]
        B1["bronze.* tables<br/>text + load metadata"]
        B2["load_ledger<br/>(content hash: idempotent)"]
        B3["quarantine_files<br/>(schema drift waits here)"]
    end
    subgraph SILVER["silver: clean"]
        S1["typed, deduplicated,<br/>status mapped, times in IST"]
        S2["rejected_rows<br/>(nothing silently dropped)"]
    end
    subgraph GOLD["gold: star schema"]
        G1["fact_orders (monthly partitions)<br/>fact_order_items, fact_refunds"]
        G2["dim_customer (SCD2), dim_restaurant,<br/>dim_partner, dim_city, dim_date"]
        G3["customer_pii (no grants to the assistant)"]
    end
    subgraph SEM["what the assistant may read"]
        V["8 analytics views<br/>no personal data"]
        C["semantic layer<br/>metrics.yml, catalog.yml, glossary.md"]
    end
    DQ{{"45 data quality checks<br/>blocking vs warning"}}
    SRC -->|"make bronze"| B1
    B1 --- B2
    B1 -. "columns changed" .-> B3
    B1 -->|"make silver"| S1
    S1 --- S2
    S1 -->|"make gold"| G1
    S1 --> G2
    S1 --> G3
    G1 --> DQ
    G2 --> DQ
    G1 --> V
    G2 --> V
    V --> A["the assistant"]
    C --> A
    DQ -. "blocking failure: do not publish,<br/>warn readers" .-> A
```

Each stage keeps its own high-water mark (`ops.pipeline_state`), so a run only touches new
batches. `make daily` runs the whole chain and stops before publishing if a blocking check fails
(see d).

## b) The agent graph (one question)

```mermaid
flowchart TD
    START([question]) --> router{router}
    router -->|ambiguous| clarifier --> finalize
    router -->|unsafe or out of scope| refusal --> finalize
    router -->|simple| plan_simple --> schema_agent
    router -->|multi_step| planner --> schema_agent
    schema_agent --> sql_writer --> guard{guard}
    guard -->|ok| cost_check{cost_check}
    guard -->|repairable syntax| healer
    guard -->|refused: final| record_result
    cost_check -->|too expensive: rewrite| sql_writer
    cost_check -->|ok| executor
    cost_check -->|healable error| healer
    cost_check -->|gives up| record_result
    executor -->|ran| result_checker{result_checker}
    executor -->|error| healer
    executor -->|gives up| record_result
    healer -->|new query| guard
    healer -->|limit reached| record_result
    result_checker -->|suspect: back with a reason| sql_writer
    result_checker -->|ok| record_result
    record_result -->|next step| schema_agent
    record_result -->|one step| narrator --> finalize
    record_result -->|several steps| synthesizer --> finalize
    finalize --> END([answer])
```

The router, clarifier, planner, SQL writer, healer, narrator and synthesizer use a model. The
guard, cost check, executor, result checker, schema agent and every routing decision are plain
code. The model proposes; code decides.

## c) Security layers

```mermaid
flowchart LR
    K["API key"] -->|"SHA-256 hash"| U["ops.app_users<br/>user + allowed cities"]
    U --> Q["Question"]
    Q --> S0["0. Code screen<br/>refuses writes, PII requests,<br/>rule overrides"]
    S0 --> G["1. SQL guard (sqlglot)<br/>exactly one SELECT,<br/>allow-listed views, adds LIMIT"]
    G --> R["2. assistant_ro role<br/>SELECT on 8 views only"]
    R --> L["3. Session limits<br/>read-only, 8 s timeout,<br/>set_config revoked"]
    L --> V["4. Views<br/>no personal data"]
    V --> RLS["5. Row-level security<br/>only the caller's cities,<br/>no scope declared = no rows"]
    RLS --> D[("gold")]
    N["Answer text"] -.->|"every number checked<br/>against the result rows"| Q
```

| Layer | Where |
|---|---|
| Code screen | `agents/router.py` |
| SQL guard | `security/sql_guard.py`, `agents/guard_node.py` |
| Role and grants | `sql/security/00_roles.sql` (grants follow `semantic/catalog.yml`) |
| Session limits | `ALTER ROLE assistant_ro SET ...` |
| Views | `sql/security/10_analytics_views.sql` |
| Row-level security | `sql/security/05_rls.sql`, `security/scope.py` |
| Verifier | `make security-verify` fails if any layer is weakened |

## d) A request, and the daily run

```mermaid
flowchart TD
    subgraph ASK["Asking"]
        UI["chat page"] --> AS
        API["POST /ask"] --> AS
        CLI["command line"] --> AS
        AS["Assistant.for_app()"] --> CK{"answer cache<br/>question + data version + city scope"}
        CK -->|hit| ANS["answer + SQL + attempts"]
        CK -->|miss| GR["agent graph (b)"] --> ANS
        GR --> AU[("ops.assistant_runs<br/>scrubbed audit trail")]
    end
    subgraph DAILY["make daily (06:00 by the scheduler)"]
        D1["bronze"] --> D2["silver"] --> D3["gold"] --> D4{"data quality"}
        D4 -->|"blocking failure"| STOP["stop: no new version,<br/>run recorded as blocked"]
        D4 -->|ok| D5["refresh catalog"] --> D6["publish: data version + 1"]
    end
    D6 -->|"retires cached answers,<br/>assistant reloads the catalog"| CK
```

`ops.pipeline_runs` records every daily run, including the blocked and failed ones.
