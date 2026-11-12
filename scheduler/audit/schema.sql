CREATE TABLE IF NOT EXISTS batches (
  seq INTEGER PRIMARY KEY AUTOINCREMENT,
  id TEXT NOT NULL UNIQUE,
  at_ms INTEGER NOT NULL,
  received_ms INTEGER NOT NULL,
  kind TEXT NOT NULL,
  payload BLOB NOT NULL
);
CREATE INDEX IF NOT EXISTS batches_at ON batches(at_ms);
CREATE INDEX IF NOT EXISTS batches_kind_at ON batches(kind, at_ms);
CREATE INDEX IF NOT EXISTS batches_kind_received ON batches(kind, received_ms);
CREATE TABLE IF NOT EXISTS operational_state (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  at_ms INTEGER NOT NULL,
  payload BLOB NOT NULL
);
