-- Row-level security: which cities may this database session see?
-- Run as etl_writer (the table owner). Safe to repeat.
--
-- The rule, in one place (gold.visible_city_sks):
--   * pipeline and admin sessions (not members of app_restricted): every city
--   * restricted sessions must DECLARE a scope:  SET app.allowed_cities = 'ALL' | 'Pune,Mumbai'
--       - no scope declared               -> no rows (fail closed)
--       - scope names cities              -> only those, and only if the role's ceiling in
--                                            ops.role_city_access also allows them
-- It reads session_user, not current_user, because the views run with their owner's rights.

CREATE OR REPLACE FUNCTION gold.visible_city_sks() RETURNS SETOF integer
LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog, gold, ops
AS $$
DECLARE
    scope text := nullif(btrim(current_setting('app.allowed_cities', true)), '');
BEGIN
    -- pg_has_role() says "yes" for every superuser, so superusers are named explicitly
    IF (SELECT rolsuper FROM pg_roles WHERE rolname = session_user)
       OR NOT pg_has_role(session_user, 'app_restricted', 'member') THEN
        RETURN QUERY SELECT city_sk FROM gold.dim_city;
        RETURN;
    END IF;
    IF scope IS NULL THEN
        RETURN;  -- fail closed: a restricted session that did not declare a scope sees nothing
    END IF;
    RETURN QUERY
    SELECT c.city_sk
    FROM gold.dim_city c
    WHERE EXISTS (SELECT 1 FROM ops.role_city_access a
                  WHERE a.role_name = session_user::text AND a.city_name IN ('*', c.city_name))
      AND (upper(scope) = 'ALL'
           OR c.city_name = ANY (ARRAY(SELECT btrim(x) FROM unnest(string_to_array(scope, ',')) x)));
END $$;

-- FORCE makes the rule apply even though etl_writer owns the tables. That matters because the
-- analytics views run with their owner's rights: without FORCE they would skip the policy.
ALTER TABLE gold.fact_orders      ENABLE ROW LEVEL SECURITY;
ALTER TABLE gold.fact_orders      FORCE  ROW LEVEL SECURITY;
DROP POLICY IF EXISTS city_scope ON gold.fact_orders;
CREATE POLICY city_scope ON gold.fact_orders
    USING (city_sk IN (SELECT gold.visible_city_sks()));

ALTER TABLE gold.fact_refunds     ENABLE ROW LEVEL SECURITY;
ALTER TABLE gold.fact_refunds     FORCE  ROW LEVEL SECURITY;
DROP POLICY IF EXISTS city_scope ON gold.fact_refunds;
CREATE POLICY city_scope ON gold.fact_refunds
    USING (city_sk IN (SELECT gold.visible_city_sks()));

ALTER TABLE gold.daily_city_sales ENABLE ROW LEVEL SECURITY;
ALTER TABLE gold.daily_city_sales FORCE  ROW LEVEL SECURITY;
DROP POLICY IF EXISTS city_scope ON gold.daily_city_sales;
CREATE POLICY city_scope ON gold.daily_city_sales
    USING (city_sk IN (SELECT gold.visible_city_sks()));

ALTER TABLE gold.dim_restaurant   ENABLE ROW LEVEL SECURITY;
ALTER TABLE gold.dim_restaurant   FORCE  ROW LEVEL SECURITY;
DROP POLICY IF EXISTS city_scope ON gold.dim_restaurant;
CREATE POLICY city_scope ON gold.dim_restaurant
    USING (city_sk IN (SELECT gold.visible_city_sks()));

-- item lines have no city of their own: they follow their restaurant
ALTER TABLE gold.fact_order_items ENABLE ROW LEVEL SECURITY;
ALTER TABLE gold.fact_order_items FORCE  ROW LEVEL SECURITY;
DROP POLICY IF EXISTS city_scope ON gold.fact_order_items;
CREATE POLICY city_scope ON gold.fact_order_items
    USING (restaurant_sk IN (SELECT r.restaurant_sk FROM gold.dim_restaurant r
                             WHERE r.city_sk IN (SELECT gold.visible_city_sks())));
