-- Tier-0 journal DB (engineer-loop spec 2026-06-09 §5.2, decisions 10/11).
-- SEPARATE file knowledge/journal.sqlite — gitignored, high-volume EVIDENCE.
-- Conclusions live in knowledge.sqlite/heuristics.json. Append-only tables.

CREATE TABLE IF NOT EXISTS actions (
    action_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    ts               TEXT NOT NULL,
    project_path     TEXT NOT NULL,
    run_id           TEXT,              -- back-filled at ingest (run_id minted then)
    fix_session_id   TEXT,
    design           TEXT,
    platform         TEXT,
    actor            TEXT NOT NULL,     -- loop | agent | operator
    action_type      TEXT NOT NULL,     -- config_knob_delta|sdc_edit|stage_rerun|
                                        -- tool_invoke|escalate|ab_launch|promote|demote
    payload_json     TEXT,              -- knob old/new, cmd, exit code, duration, log path
    parent_action_id INTEGER,           -- groups a stacked fix
    symptom_id       TEXT               -- the bug being acted on (nullable)
);
CREATE INDEX IF NOT EXISTS idx_actions_project ON actions(project_path);
CREATE INDEX IF NOT EXISTS idx_actions_run     ON actions(run_id);
CREATE INDEX IF NOT EXISTS idx_actions_session ON actions(fix_session_id);
CREATE INDEX IF NOT EXISTS idx_actions_symptom ON actions(symptom_id);

CREATE TABLE IF NOT EXISTS log_summaries (
    summary_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    ts            TEXT NOT NULL,
    project_path  TEXT NOT NULL,
    run_id        TEXT,                 -- back-filled at ingest
    action_id     INTEGER,              -- the producing command's actions row
    stage         TEXT,
    tool          TEXT,
    source_path   TEXT,                 -- the raw log/report file (may rotate away)
    status        TEXT,                 -- pass | fail | unknown
    error_count   INTEGER,
    warning_count INTEGER,
    first_error   TEXT,
    last_lines    TEXT,                 -- bounded tail, only on failure
    metrics_json  TEXT,                 -- key numbers (wns, violation counts, ...)
    digest        TEXT                  -- compact deterministic text summary
);
CREATE INDEX IF NOT EXISTS idx_summaries_project ON log_summaries(project_path);
CREATE INDEX IF NOT EXISTS idx_summaries_run     ON log_summaries(run_id);

CREATE TABLE IF NOT EXISTS tool_bugs (
    bug_id         INTEGER PRIMARY KEY AUTOINCREMENT,
    ts             TEXT NOT NULL,
    project_path   TEXT NOT NULL,
    run_id         TEXT,                -- back-filled at ingest
    action_id      INTEGER,
    stage          TEXT,
    tool           TEXT,
    signature      TEXT,                -- normalized error line
    symptom_id     TEXT,                -- cross-DB bug identity (decision 11)
    signature_json TEXT,
    log_excerpt    TEXT
);
CREATE INDEX IF NOT EXISTS idx_bugs_project ON tool_bugs(project_path);
CREATE INDEX IF NOT EXISTS idx_bugs_symptom ON tool_bugs(symptom_id);
