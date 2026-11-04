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

CREATE TABLE IF NOT EXISTS user_preferences (
    user_id bigint PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    document jsonb NOT NULL DEFAULT '{}',
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS workers (
    id integer GENERATED ALWAYS AS IDENTITY
        (START WITH 1000 MINVALUE 1000 MAXVALUE 9999 NO CYCLE) PRIMARY KEY,
    first_name text NOT NULL CHECK (length(btrim(first_name)) BETWEEN 1 AND 100),
    last_name text NOT NULL CHECK (length(btrim(last_name)) BETWEEN 1 AND 100),
    photo bytea CHECK (photo IS NULL OR octet_length(photo) <= 5242880),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CHECK (id BETWEEN 1000 AND 9999)
);
ALTER TABLE events ADD COLUMN IF NOT EXISTS workspace_id text;
CREATE INDEX IF NOT EXISTS events_workspace_time_idx ON events(workspace_id, id DESC);

-- Assignments are stored with the existing workspace configuration. An empty
-- inspector list is intentionally private to administrators.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM schema_version WHERE version=3) THEN
        INSERT INTO user_preferences(user_id, document)
        SELECT u.id, jsonb_build_object(
            'theme', COALESCE(c.document #> '{settings,theme}', '"Light"'::jsonb),
            'view_columns', COALESCE(c.document #> '{settings,view_columns}', '2'::jsonb),
            'auto_connect', COALESCE(c.document #> '{settings,auto_connect}', 'true'::jsonb),
            'reconnect_seconds', COALESCE(c.document #> '{settings,reconnect_seconds}', '5'::jsonb))
        FROM users u CROSS JOIN application_config c WHERE c.id=1
        ON CONFLICT (user_id) DO NOTHING;

        UPDATE application_config c SET document=jsonb_set(c.document, '{workspaces}',
            COALESCE((SELECT jsonb_agg(w || jsonb_build_object(
                'inspector_ids', COALESCE(w->'inspector_ids', '[]'::jsonb),
                'worker_ids', COALESCE(w->'worker_ids', '[]'::jsonb)))
                FROM jsonb_array_elements(c.document->'workspaces') w), '[]'::jsonb)),
            revision=revision+1, updated_at=now();

        -- Prefer the stable camera ID. A unique former workspace name is the
        -- fallback for older events whose camera has since been removed.
        UPDATE events e SET workspace_id=(
            SELECT w->>'id' FROM application_config c,
                LATERAL jsonb_array_elements(c.document->'workspaces') w
            WHERE c.id=1 AND EXISTS (
                SELECT 1 FROM jsonb_array_elements(w->'cameras') camera
                WHERE camera->>'id'=e.camera_id) LIMIT 1)
        WHERE e.workspace_id IS NULL;
        UPDATE events e SET workspace_id=(
            SELECT min(w->>'id') FROM application_config c,
                LATERAL jsonb_array_elements(c.document->'workspaces') w
            WHERE c.id=1 AND w->>'name'=e.workspace_name HAVING count(*)=1)
        WHERE e.workspace_id IS NULL;
        INSERT INTO schema_version(version) VALUES (3);
    END IF;
END $$;

CREATE SEQUENCE IF NOT EXISTS camera_display_id_seq AS integer
    START WITH 1000 MINVALUE 1000 MAXVALUE 9999 NO CYCLE;
DO $$
DECLARE
    configuration jsonb;
    updated_workspaces jsonb := '[]'::jsonb;
    updated_cameras jsonb;
    workspace jsonb;
    camera jsonb;
BEGIN
    IF NOT EXISTS (SELECT 1 FROM schema_version WHERE version=4) THEN
        SELECT document INTO configuration FROM application_config WHERE id=1 FOR UPDATE;
        IF FOUND THEN
            -- Array order, UUIDs, assignments and camera settings are retained.
            FOR workspace IN SELECT value FROM jsonb_array_elements(configuration->'workspaces')
            LOOP
                updated_cameras := '[]'::jsonb;
                FOR camera IN SELECT value FROM jsonb_array_elements(workspace->'cameras')
                LOOP
                    updated_cameras := updated_cameras || jsonb_build_array(camera ||
                        jsonb_build_object('display_id', nextval('camera_display_id_seq')));
                END LOOP;
                updated_workspaces := updated_workspaces || jsonb_build_array(
                    jsonb_set(workspace, '{cameras}', updated_cameras));
            END LOOP;
            UPDATE application_config SET document=jsonb_set(configuration, '{workspaces}', updated_workspaces),
                revision=revision+1, updated_at=now() WHERE id=1;
        END IF;
        INSERT INTO schema_version(version) VALUES (4);
    END IF;
END $$;
