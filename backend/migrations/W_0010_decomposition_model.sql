CREATE TABLE decomposition_model_settings (
 id INTEGER PRIMARY KEY CHECK(id=1), endpoint TEXT NOT NULL, model TEXT NOT NULL,
 encrypted_key TEXT, revision INTEGER NOT NULL CHECK(revision>0), updated_at TEXT NOT NULL,
 reasoning_effort TEXT NOT NULL CHECK(reasoning_effort IN ('none','low','medium','xhigh')),
 temperature REAL NOT NULL CHECK(temperature>=0 AND temperature<=1)
);
-- Existing installations get an independent text model, with a one-time copy
-- of service credentials. Future changes never synchronize these two rows.
INSERT INTO decomposition_model_settings
 SELECT 1,endpoint,'qwen3.8-flash',encrypted_key,1,updated_at,'xhigh',0.1
 FROM evidence_model_settings WHERE id=1;
