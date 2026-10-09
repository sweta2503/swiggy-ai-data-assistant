-- Safe casts: bad text becomes NULL instead of crashing the whole load.
-- Silver then decides what to do with the NULL (reject the row with a reason).

CREATE OR REPLACE FUNCTION silver.try_numeric(v text) RETURNS numeric
LANGUAGE sql IMMUTABLE AS $$
    SELECT CASE WHEN trim(v) ~ '^-?[0-9]+(\.[0-9]+)?$' THEN trim(v)::numeric END
$$;

-- UTC timestamps from the app: 2026-03-01T06:42:11Z
CREATE OR REPLACE FUNCTION silver.try_timestamptz_utc(v text) RETURNS timestamptz
LANGUAGE plpgsql IMMUTABLE AS $$
BEGIN
    IF trim(v) !~ '^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$' THEN RETURN NULL; END IF;
    RETURN trim(v)::timestamptz;
EXCEPTION WHEN others THEN RETURN NULL;   -- e.g. month 13
END $$;

-- Naive timestamps from the payment gateway: 2026-03-01 12:07:45 (no zone marker)
CREATE OR REPLACE FUNCTION silver.try_timestamp(v text) RETURNS timestamp
LANGUAGE plpgsql IMMUTABLE AS $$
BEGIN
    IF trim(v) !~ '^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$' THEN RETURN NULL; END IF;
    RETURN trim(v)::timestamp;
EXCEPTION WHEN others THEN RETURN NULL;
END $$;

CREATE OR REPLACE FUNCTION silver.try_date(v text) RETURNS date
LANGUAGE plpgsql IMMUTABLE AS $$
BEGIN
    IF trim(v) !~ '^\d{4}-\d{2}-\d{2}$' THEN RETURN NULL; END IF;
    RETURN trim(v)::date;
EXCEPTION WHEN others THEN RETURN NULL;
END $$;
