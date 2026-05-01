-- Sidecar schema. Time fields are unix epoch seconds (REAL).

CREATE TABLE IF NOT EXISTS claims (
  kind         TEXT    NOT NULL,
  key          TEXT    NOT NULL,
  drone_id     TEXT    NOT NULL,
  acquired_at  REAL    NOT NULL,
  expires_at   REAL    NOT NULL,
  cancelled    INTEGER NOT NULL DEFAULT 0,
  metadata     TEXT,
  PRIMARY KEY (kind, key)
);
CREATE INDEX IF NOT EXISTS idx_claims_drone   ON claims(drone_id);
CREATE INDEX IF NOT EXISTS idx_claims_expires ON claims(expires_at);

CREATE TABLE IF NOT EXISTS installs (
  key               TEXT    PRIMARY KEY,
  installed_by      TEXT    NOT NULL,
  installed_at      REAL    NOT NULL,
  install_commands  TEXT    NOT NULL,   -- JSON list
  usage             TEXT
);

CREATE TABLE IF NOT EXISTS provider_buckets (
  provider          TEXT    PRIMARY KEY,
  capacity_tokens   INTEGER NOT NULL,
  tokens_remaining  REAL    NOT NULL,   -- fractional refill needs REAL
  refill_per_sec    REAL    NOT NULL,
  last_refill_at    REAL    NOT NULL
);

CREATE TABLE IF NOT EXISTS cost_meter (
  run_id       TEXT PRIMARY KEY,
  ceiling_usd  REAL,
  spent_usd    REAL NOT NULL DEFAULT 0,
  started_at   REAL NOT NULL
);
