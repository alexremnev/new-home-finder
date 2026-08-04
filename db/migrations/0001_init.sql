-- 0001_init.sql — POC schema.
--
-- Design notes that are not obvious from the DDL:
--   * Extraction selectors live in parse_schemas as data, not in code, because
--     they are rewritten automatically when a site changes markup.
--   * notifications is keyed by (user_id, listing_id), not by subscription, so
--     send history survives deletion of a search filter.
--   * Schedules live in the database so intervals can be changed per source
--     without a deploy.
--   * Nothing is hard-deleted except on an explicit erasure request: delisting
--     is a status change, and history is required for analytics.

BEGIN;

-- ═══════════════════════════════ reference ═══════════════════════════════

CREATE TABLE sources (
    key             TEXT PRIMARY KEY,
    display_name    TEXT    NOT NULL,
    enabled         BOOLEAN NOT NULL DEFAULT true,
    min_items       INT     NOT NULL DEFAULT 5,   -- health gate floor
    health          TEXT    NOT NULL DEFAULT 'ok',
    health_note     TEXT,
    health_until    TIMESTAMPTZ,                  -- circuit-breaker cooldown
    config          JSONB   NOT NULL DEFAULT '{}',
    CONSTRAINT sources_health_ck
        CHECK (health IN ('ok', 'degraded', 'broken', 'blocked'))
);

CREATE TABLE channels (
    key          TEXT PRIMARY KEY,
    display_name TEXT    NOT NULL,
    enabled      BOOLEAN NOT NULL DEFAULT true,
    config       JSONB   NOT NULL DEFAULT '{}'
);

CREATE TABLE locations (
    id           BIGSERIAL PRIMARY KEY,
    kind         TEXT NOT NULL,          -- postcode_district | borough | city
    code         TEXT NOT NULL,          -- 'E14', 'london'
    city         TEXT NOT NULL DEFAULT 'London',
    tfl_zone_min SMALLINT,
    tfl_zone_max SMALLINT,
    approx       BOOLEAN NOT NULL DEFAULT false,  -- zone inferred, not authoritative
    lat          DOUBLE PRECISION,
    lng          DOUBLE PRECISION,
    UNIQUE (kind, code)
);

-- Each portal names the same place differently; the mapping is data so that
-- adding a city does not require code changes.
CREATE TABLE source_locations (
    source_key  TEXT   NOT NULL REFERENCES sources(key) ON DELETE CASCADE,
    location_id BIGINT NOT NULL REFERENCES locations(id) ON DELETE CASCADE,
    external_id TEXT   NOT NULL,
    enabled     BOOLEAN NOT NULL DEFAULT true,
    PRIMARY KEY (source_key, location_id)
);

-- ═══════════════════════════════ extraction ══════════════════════════════

CREATE TABLE parse_schemas (
    id                 BIGSERIAL PRIMARY KEY,
    source_key         TEXT  NOT NULL REFERENCES sources(key) ON DELETE CASCADE,
    page_kind          TEXT  NOT NULL,            -- search_list | detail
    version            INT   NOT NULL,
    layout_fingerprint TEXT  NOT NULL,            -- structural hash of the markup
    strategy           TEXT  NOT NULL,
    schema             JSONB NOT NULL,            -- ExtractionSchema
    status             TEXT  NOT NULL,
    created_by         TEXT  NOT NULL,            -- 'ai:claude-haiku-4-5' | 'human'
    validation         JSONB,                     -- Health at activation time
    sample_ref         TEXT,                      -- snapshot of the body it was built from
    tokens_in          INT,
    tokens_out         INT,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    activated_at       TIMESTAMPTZ,
    UNIQUE (source_key, page_kind, version),
    CONSTRAINT parse_schemas_strategy_ck
        CHECK (strategy IN ('next_data', 'api_json', 'jsonld', 'dom')),
    CONSTRAINT parse_schemas_status_ck
        CHECK (status IN ('active', 'pinned', 'candidate', 'retired', 'failed'))
);

-- At most one schema in force per (source, page kind).
CREATE UNIQUE INDEX parse_schemas_one_live
    ON parse_schemas (source_key, page_kind)
    WHERE status IN ('active', 'pinned');

-- Lets a retired schema be reactivated for free when a site reverts an A/B test.
CREATE INDEX parse_schemas_by_fingerprint
    ON parse_schemas (source_key, page_kind, layout_fingerprint);

-- ═══════════════════════════════ listings ════════════════════════════════

CREATE TABLE listings (
    id                 BIGSERIAL PRIMARY KEY,
    source_key         TEXT NOT NULL REFERENCES sources(key) ON DELETE CASCADE,
    external_id        TEXT NOT NULL,
    url                TEXT NOT NULL,

    price_pcm          INTEGER  NOT NULL,          -- always GBP per calendar month
    bedrooms           SMALLINT NOT NULL,          -- studio is 0
    bathrooms          SMALLINT,
    property_type      TEXT,
    furnished          TEXT NOT NULL DEFAULT 'unknown',
    pets_allowed       BOOLEAN,                    -- NULL means not stated
    bills_included     BOOLEAN,
    available_from     DATE,
    min_tenancy_months SMALLINT,
    deposit_pcm        REAL,
    postcode           TEXT,
    postcode_district  TEXT,
    tfl_zone           SMALLINT,
    lat DOUBLE PRECISION,
    lng DOUBLE PRECISION,
    title              TEXT,
    description        TEXT,
    is_landlord_direct BOOLEAN,
    photo_count        SMALLINT,

    -- Reserved for cross-portal deduplication, unused in the POC. Present now
    -- so that enabling it later is a backfill rather than a migration.
    dedupe_key         TEXT,
    cluster_id         BIGINT,

    status             TEXT     NOT NULL DEFAULT 'active',
    miss_count         SMALLINT NOT NULL DEFAULT 0,
    first_seen_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    delisted_at        TIMESTAMPTZ,

    schema_id          BIGINT REFERENCES parse_schemas(id) ON DELETE SET NULL,
    raw                JSONB NOT NULL,

    UNIQUE (source_key, external_id),              -- re-running a scrape is safe
    CONSTRAINT listings_status_ck CHECK (status IN ('active', 'delisted')),
    CONSTRAINT listings_furnished_ck
        CHECK (furnished IN ('furnished', 'unfurnished', 'part', 'unknown')),
    CONSTRAINT listings_price_ck CHECK (price_pcm BETWEEN 100 AND 100000),
    CONSTRAINT listings_bedrooms_ck CHECK (bedrooms BETWEEN 0 AND 20)
);

CREATE INDEX listings_match
    ON listings (postcode_district, price_pcm, bedrooms) WHERE status = 'active';
CREATE INDEX listings_fresh
    ON listings (first_seen_at DESC) WHERE status = 'active';
CREATE INDEX listings_cluster ON listings (cluster_id) WHERE cluster_id IS NOT NULL;

-- Written from the first day so that price-change alerts, when enabled, have
-- history behind them rather than starting from zero.
CREATE TABLE listing_price_log (
    listing_id BIGINT  NOT NULL REFERENCES listings(id) ON DELETE CASCADE,
    price_pcm  INTEGER NOT NULL,
    seen_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (listing_id, seen_at)
);

-- ═══════════════════════════════ users ══════════════════════════════════

CREATE TABLE users (
    id             BIGSERIAL PRIMARY KEY,
    status         TEXT NOT NULL DEFAULT 'pending',
    consent_at     TIMESTAMPTZ,          -- proof of opt-in; required under PECR
    consent_source TEXT,
    stopped_at     TIMESTAMPTZ,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT users_status_ck
        CHECK (status IN ('pending', 'active', 'stopped', 'blocked'))
);

-- Adding a delivery channel is a row here, not a schema change.
CREATE TABLE user_channels (
    user_id     BIGINT  NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    channel     TEXT    NOT NULL REFERENCES channels(key),
    address     TEXT    NOT NULL,        -- chat id, E.164 phone, or email
    is_primary  BOOLEAN NOT NULL DEFAULT true,
    verified_at TIMESTAMPTZ,
    PRIMARY KEY (user_id, channel),
    UNIQUE (channel, address)
);

CREATE TABLE subscriptions (
    id                 BIGSERIAL PRIMARY KEY,
    user_id            BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    label              TEXT,
    criteria           JSONB  NOT NULL,
    active             BOOLEAN NOT NULL DEFAULT true,
    max_alerts_per_day SMALLINT NOT NULL DEFAULT 10,
    -- Only listings first seen after this instant are eligible, so a new
    -- subscription does not replay the existing backlog.
    backfill_from      TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX subscriptions_active ON subscriptions (user_id) WHERE active;

-- ═══════════════════════════════ outbox ═════════════════════════════════

CREATE TABLE notifications (
    id              BIGSERIAL PRIMARY KEY,
    user_id         BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    -- SET NULL, not CASCADE: deleting a filter must not erase send history,
    -- otherwise a returning user is sent the same listings again.
    subscription_id BIGINT REFERENCES subscriptions(id) ON DELETE SET NULL,
    listing_id      BIGINT NOT NULL REFERENCES listings(id) ON DELETE CASCADE,
    channel         TEXT   NOT NULL REFERENCES channels(key),
    kind            TEXT   NOT NULL DEFAULT 'new_listing',
    status          TEXT   NOT NULL DEFAULT 'queued',
    attempts        SMALLINT NOT NULL DEFAULT 0,
    provider_msg_id TEXT,
    error           TEXT,
    cost_micros     INTEGER NOT NULL DEFAULT 0,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    sent_at         TIMESTAMPTZ,
    -- Idempotency of delivery. Adding price-drop alerts later requires
    -- extending this to include kind; that migration is known in advance.
    UNIQUE (user_id, listing_id),
    CONSTRAINT notifications_status_ck
        CHECK (status IN ('queued', 'sent', 'failed', 'skipped'))
);
CREATE INDEX notifications_queue
    ON notifications (created_at) WHERE status = 'queued';

-- ═══════════════════════════════ scheduling ═════════════════════════════

-- The cron trigger only ticks; this table decides what is due. Changing an
-- interval is an UPDATE, not a commit, and it is per source.
CREATE TABLE schedules (
    id                BIGSERIAL PRIMARY KEY,
    job               TEXT NOT NULL,               -- hot | sweep | drain
    source_key        TEXT REFERENCES sources(key) ON DELETE CASCADE,
    enabled           BOOLEAN  NOT NULL DEFAULT true,
    interval_seconds  INT      NOT NULL,
    jitter_pct        SMALLINT NOT NULL DEFAULT 20,
    quiet_hours       JSONB    NOT NULL DEFAULT '{}',
    max_requests      INT      NOT NULL DEFAULT 200,
    max_runtime_s     INT      NOT NULL DEFAULT 480,
    next_run_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_run_at       TIMESTAMPTZ,
    last_status       TEXT,
    consecutive_fails SMALLINT NOT NULL DEFAULT 0,
    CONSTRAINT schedules_interval_ck CHECK (interval_seconds >= 60),
    CONSTRAINT schedules_jitter_ck CHECK (jitter_pct BETWEEN 0 AND 50)
);
-- One schedule per job per source; NULL source_key means "all sources".
CREATE UNIQUE INDEX schedules_job_source
    ON schedules (job, coalesce(source_key, '*'));
CREATE INDEX schedules_due ON schedules (next_run_at) WHERE enabled;

-- ═══════════════════════════════ observability ══════════════════════════

CREATE TABLE job_runs (
    id          BIGSERIAL PRIMARY KEY,
    job         TEXT NOT NULL,
    trigger     TEXT NOT NULL,                     -- schedule | manual | retry
    run_url     TEXT,                              -- link back to the CI run, if any
    started_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at TIMESTAMPTZ,
    status      TEXT NOT NULL DEFAULT 'running',
    counters    JSONB NOT NULL DEFAULT '{}',
    error       TEXT,
    CONSTRAINT job_runs_status_ck
        CHECK (status IN ('running', 'ok', 'degraded', 'failed', 'skipped_locked'))
);
CREATE INDEX job_runs_recent ON job_runs (started_at DESC);

CREATE TABLE job_stages (
    id          BIGSERIAL PRIMARY KEY,
    run_id      BIGINT NOT NULL REFERENCES job_runs(id) ON DELETE CASCADE,
    stage       TEXT   NOT NULL,
    source_key  TEXT,
    started_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at TIMESTAMPTZ,
    status      TEXT   NOT NULL DEFAULT 'running',
    counters    JSONB  NOT NULL DEFAULT '{}'
);
CREATE INDEX job_stages_run ON job_stages (run_id);

-- The repository is public, so this table must never receive a chat id, phone
-- number, email address, or a full criteria object. Reference users by id.
CREATE TABLE job_events (
    id         BIGSERIAL PRIMARY KEY,
    run_id     BIGINT NOT NULL REFERENCES job_runs(id) ON DELETE CASCADE,
    ts         TIMESTAMPTZ NOT NULL DEFAULT now(),
    level      TEXT NOT NULL,
    stage      TEXT,
    source_key TEXT,
    message    TEXT NOT NULL,
    ctx        JSONB NOT NULL DEFAULT '{}',
    CONSTRAINT job_events_level_ck CHECK (level IN ('debug', 'info', 'warn', 'error'))
);
CREATE INDEX job_events_run ON job_events (run_id, ts);
CREATE INDEX job_events_problems ON job_events (ts DESC) WHERE level IN ('warn', 'error');

-- ═══════════════════════════════ seed ═══════════════════════════════════

INSERT INTO channels (key, display_name, enabled) VALUES
    ('telegram', 'Telegram', true),
    ('whatsapp', 'WhatsApp', false),
    ('email',    'Email',    false);

INSERT INTO sources (key, display_name, enabled, min_items, config) VALUES (
    'openrent', 'OpenRent', true, 5,
    '{
       "user_agent_mode": "auto",
       "impersonate": "chrome124",
       "rate_limit_rps": 0.3,
       "sweep_rate_limit_rps": 0.15,
       "concurrency": 1,
       "shuffle_urls": true,
       "respect_robots": true,
       "conditional_requests": true,
       "jitter": {"between_requests": "exponential", "min_factor": 0.4, "max_factor": 3.0},
       "retry": {"max_attempts": 3, "base_seconds": 5, "max_seconds": 120},
       "breaker": {"on": ["403", "503", "challenge"],
                   "cooldown_base_minutes": 30, "cooldown_max_hours": 12},
       "proxy": {"enabled": false, "provider": null},
       "discovery": {
         "primary":  {"kind": "sitemap_diff", "enabled": true,
                      "index": "https://www.openrent.co.uk/sitemap.xml",
                      "children": ["sitemap-listings-1.xml", "sitemap-listings-2.xml"]},
         "fallback": {"kind": "search_api", "enabled": false}
       }
     }'::jsonb
);

INSERT INTO schedules (job, source_key, interval_seconds, quiet_hours) VALUES
    ('hot',   'openrent', 900,
     '{"tz": "Europe/London", "from": "23:00", "to": "07:00", "mode": "queue"}'::jsonb),
    ('sweep', 'openrent', 86400, '{}'::jsonb),
    ('drain', NULL,       300,   '{}'::jsonb);

COMMIT;
