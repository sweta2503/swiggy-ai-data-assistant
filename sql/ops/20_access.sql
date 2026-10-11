-- Who may see what.
--
-- app_users: the people (or API keys) that talk to the assistant, and the cities each may see.
--            Only a SHA-256 hash of the API key is stored, never the key itself.
-- role_city_access: the MOST a database role may ever see (a ceiling). '*' means every city.
--            A request can narrow its view below this ceiling but never widen it.
CREATE TABLE IF NOT EXISTS ops.app_users (
    user_id        text PRIMARY KEY,
    display_name   text        NOT NULL,
    api_key_hash   text        NOT NULL UNIQUE,
    allowed_cities text        NOT NULL,      -- 'ALL' or a comma-separated list of city names
    is_active      boolean     NOT NULL DEFAULT true,
    created_at     timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS ops.role_city_access (
    role_name text NOT NULL,
    city_name text NOT NULL,                  -- a city, or '*' for all
    PRIMARY KEY (role_name, city_name)
);

-- the assistant's login may see every city; each request narrows that to its user's cities
INSERT INTO ops.role_city_access VALUES ('assistant_ro', '*') ON CONFLICT DO NOTHING;
