CREATE TABLE IF NOT EXISTS schema_version (
    version integer PRIMARY KEY,
    installed_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS users (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    username text NOT NULL UNIQUE,
    display_name text NOT NULL,
    role text NOT NULL CHECK (role IN ('admin', 'inspector')),
    password_hash text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS application_config (
    id integer PRIMARY KEY CHECK (id = 1),
    document jsonb NOT NULL,
    revision bigint NOT NULL DEFAULT 1,
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS events (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    occurred_at timestamptz NOT NULL DEFAULT now(),
    camera_id text NOT NULL,
    camera_name text NOT NULL,
    workspace_name text NOT NULL,
    kind text NOT NULL DEFAULT 'no_helmet' CHECK (kind = 'no_helmet'),
    object_count integer NOT NULL CHECK (object_count > 0),
    snapshot bytea NOT NULL,
    reviewed_by bigint REFERENCES users(id),
    reviewed_at timestamptz,
    note text NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS events_time_idx ON events(occurred_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS events_camera_time_idx ON events(camera_id, occurred_at DESC);
INSERT INTO schema_version(version) VALUES (1) ON CONFLICT DO NOTHING;
-- Upgrade existing installations without changing saved events.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM schema_version WHERE version=2) THEN
        ALTER TABLE events DROP CONSTRAINT IF EXISTS events_kind_check;
        ALTER TABLE events ADD CONSTRAINT events_kind_check
            CHECK (kind ~ '^[a-z][a-z0-9_]{0,63}$');
        INSERT INTO schema_version(version) VALUES (2);
    END IF;
END $$;
CREATE INDEX IF NOT EXISTS events_camera_kind_time_idx ON events(camera_id, kind, occurred_at DESC);
