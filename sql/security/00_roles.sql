-- Roles and database-level privileges. Run as the admin (it needs superuser rights).
-- Safe to repeat.
--
--   etl_writer          the pipeline: owns bronze/silver/gold/ops, writes data
--   assistant_ro        the assistant's login: may read the analytics views and nothing else
--   app_restricted      a marker group. Members are subject to city scoping (row-level security);
--                       sessions of non-members (pipeline, admin) are not.
--   analyst_city_role   a template group for people: give each city manager a login that is a
--                       member of this role, then list their cities in ops.role_city_access.

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'app_restricted') THEN
        CREATE ROLE app_restricted NOLOGIN;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'analyst_city_role') THEN
        CREATE ROLE analyst_city_role NOLOGIN;
    END IF;
END $$;

GRANT app_restricted TO assistant_ro;
GRANT app_restricted TO analyst_city_role;

-- No special powers, ever
ALTER ROLE assistant_ro NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS
    CONNECTION LIMIT 20;
ALTER ROLE etl_writer   NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;

-- Nobody gets anything by default. PUBLIC loses CONNECT and TEMP; we grant them back by name.
DO $$
BEGIN
    EXECUTE format('REVOKE ALL ON DATABASE %I FROM PUBLIC', current_database());
    EXECUTE format('GRANT CONNECT ON DATABASE %I TO etl_writer, assistant_ro, analyst_city_role',
                   current_database());
    EXECUTE format('GRANT TEMPORARY ON DATABASE %I TO etl_writer', current_database());
END $$;

REVOKE ALL ON SCHEMA public FROM PUBLIC;

-- The assistant gets no access to any schema except analytics (granted in the Python step,
-- view by view, from semantic/catalog.yml).
REVOKE ALL ON SCHEMA bronze, silver, gold, ops, semantic FROM assistant_ro, analyst_city_role;

-- The per-request city scope is a session setting. Calling set_config() from inside a query
-- could widen it, so only the pipeline role may call it. (The SQL guard in phase 8 also
-- rejects SET and set_config.)
REVOKE EXECUTE ON FUNCTION pg_catalog.set_config(text, text, boolean) FROM PUBLIC;
GRANT  EXECUTE ON FUNCTION pg_catalog.set_config(text, text, boolean) TO etl_writer;
