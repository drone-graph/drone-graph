-- Sidecar store for tool (and later skill) embedding vectors. Single process
-- primary user; WAL + lock pattern matches signals/sqlite.py.

CREATE TABLE IF NOT EXISTS tool_embedding (
  tool_name   TEXT    NOT NULL,
  scope       TEXT    NOT NULL,
  model_id    TEXT    NOT NULL,
  dim         INTEGER NOT NULL,
  vector      BLOB    NOT NULL,
  source_hash TEXT,
  updated_at  REAL    NOT NULL,
  PRIMARY KEY (tool_name, scope, model_id)
);

CREATE INDEX IF NOT EXISTS idx_tool_embedding_scope ON tool_embedding(scope);
