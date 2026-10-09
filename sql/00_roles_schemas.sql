-- Phase 0: schemas and the two login roles.
-- Safe to re-run by hand (every statement is guarded).
-- Detailed grants (least privilege, RLS) arrive in phase 7.

-- Roles ---------------------------------------------------------------
SELECT format('CREATE ROLE etl_writer LOGIN PASSWORD %L', :'etl_password')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'etl_writer') \gexec

SELECT format('CREATE ROLE assistant_ro LOGIN PASSWORD %L', :'assistant_password')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'assistant_ro') \gexec

-- Schemas -------------------------------------------------------------
CREATE SCHEMA IF NOT EXISTS bronze;     -- raw, as received
CREATE SCHEMA IF NOT EXISTS silver;     -- cleaned
CREATE SCHEMA IF NOT EXISTS gold;       -- star schema
CREATE SCHEMA IF NOT EXISTS analytics;  -- safe views, the ONLY thing the assistant can read
CREATE SCHEMA IF NOT EXISTS semantic;   -- metric definitions in the database
CREATE SCHEMA IF NOT EXISTS ops;        -- audit, data quality, pipeline state

-- Nobody gets anything by default. Phase 7 grants only what is needed.
REVOKE ALL ON SCHEMA public FROM PUBLIC;
GRANT USAGE, CREATE ON SCHEMA bronze, silver, gold, analytics, semantic, ops TO etl_writer;
ALTER ROLE assistant_ro SET search_path = analytics;
