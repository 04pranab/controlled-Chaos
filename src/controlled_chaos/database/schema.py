"""SQLite schema, version 1.

Design notes
* Primary keys are random UUID hex strings (stable across backups/exports).
* Instants: integer Unix seconds UTC.  Dates: ISO strings in the user's zone.
* Soft delete (``deleted_at``) on user content gives an undo window and a trash.
* Foreign keys use SET NULL for projects so archiving/deleting a project never
  removes tasks or notes.
"""

SCHEMA_V1 = """
CREATE TABLE projects (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    color TEXT NOT NULL DEFAULT '',
    archived INTEGER NOT NULL DEFAULT 0,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    deleted_at INTEGER
);

CREATE TABLE tasks (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open','done','archived')),
    importance REAL NOT NULL DEFAULT 0.5 CHECK (importance BETWEEN 0 AND 1),
    goal REAL NOT NULL DEFAULT 0 CHECK (goal BETWEEN 0 AND 1),
    user_priority INTEGER NOT NULL DEFAULT 0,
    pinned INTEGER NOT NULL DEFAULT 0,
    energy REAL NOT NULL DEFAULT 0.5 CHECK (energy BETWEEN 0 AND 1),
    deadline_ts INTEGER,
    deadline_all_day INTEGER NOT NULL DEFAULT 0,
    estimate_min REAL,
    actual_min REAL,
    project_id TEXT REFERENCES projects(id) ON DELETE SET NULL,
    category TEXT NOT NULL DEFAULT '',
    parent_id TEXT REFERENCES tasks(id) ON DELETE CASCADE,
    sort_order REAL NOT NULL DEFAULT 0,
    recurrence TEXT,
    recurrence_source_id TEXT,
    planned_date TEXT,
    priority_slot INTEGER CHECK (priority_slot IS NULL OR priority_slot BETWEEN 1 AND 3),
    priority_date TEXT,
    deferrals INTEGER NOT NULL DEFAULT 0,
    dismissed_date TEXT,
    source_inbox_id TEXT,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    completed_at INTEGER,
    deleted_at INTEGER
);
CREATE INDEX idx_tasks_status ON tasks(status, deleted_at);
CREATE INDEX idx_tasks_planned ON tasks(planned_date);
CREATE INDEX idx_tasks_project ON tasks(project_id);
CREATE INDEX idx_tasks_parent ON tasks(parent_id);

CREATE TABLE notes (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL DEFAULT '',
    body TEXT NOT NULL DEFAULT '',
    pinned INTEGER NOT NULL DEFAULT 0,
    archived INTEGER NOT NULL DEFAULT 0,
    project_id TEXT REFERENCES projects(id) ON DELETE SET NULL,
    source_inbox_id TEXT,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    deleted_at INTEGER
);
CREATE TABLE note_tags (
    note_id TEXT NOT NULL REFERENCES notes(id) ON DELETE CASCADE,
    tag TEXT NOT NULL,
    PRIMARY KEY (note_id, tag)
);
CREATE TABLE note_links (
    from_id TEXT NOT NULL REFERENCES notes(id) ON DELETE CASCADE,
    to_id TEXT NOT NULL REFERENCES notes(id) ON DELETE CASCADE,
    PRIMARY KEY (from_id, to_id)
);

CREATE TABLE journal_entries (
    id TEXT PRIMARY KEY,
    entry_date TEXT NOT NULL,
    body TEXT NOT NULL DEFAULT '',
    mood TEXT NOT NULL DEFAULT '',
    accomplishments TEXT NOT NULL DEFAULT '',
    note_tomorrow TEXT NOT NULL DEFAULT '',
    is_primary INTEGER NOT NULL DEFAULT 1,
    source_inbox_id TEXT,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    deleted_at INTEGER
);
CREATE INDEX idx_journal_date ON journal_entries(entry_date);
CREATE UNIQUE INDEX idx_journal_one_primary
    ON journal_entries(entry_date) WHERE is_primary = 1 AND deleted_at IS NULL;

CREATE TABLE calendar_events (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    notes TEXT NOT NULL DEFAULT '',
    all_day INTEGER NOT NULL DEFAULT 0,
    start_ts INTEGER,
    end_ts INTEGER,
    start_date TEXT NOT NULL,
    end_date TEXT NOT NULL,
    tz TEXT NOT NULL DEFAULT '',
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    deleted_at INTEGER,
    CHECK (all_day = 1 OR (start_ts IS NOT NULL AND end_ts IS NOT NULL AND end_ts >= start_ts)),
    CHECK (end_date >= start_date)
);
CREATE INDEX idx_events_dates ON calendar_events(start_date, end_date);

CREATE TABLE focus_sessions (
    id TEXT PRIMARY KEY,
    task_id TEXT REFERENCES tasks(id) ON DELETE SET NULL,
    kind TEXT NOT NULL DEFAULT 'focus' CHECK (kind IN ('focus','break')),
    planned_sec INTEGER NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('running','paused','completed','stopped')),
    started_ts INTEGER NOT NULL,
    accumulated_sec INTEGER NOT NULL DEFAULT 0,
    last_resume_ts INTEGER,
    ended_ts INTEGER,
    created_at INTEGER NOT NULL
);
CREATE UNIQUE INDEX idx_focus_one_active
    ON focus_sessions((1)) WHERE status IN ('running','paused');

CREATE TABLE inbox_items (
    id TEXT PRIMARY KEY,
    content TEXT NOT NULL,
    captured_at INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'new' CHECK (status IN ('new','converted','dismissed')),
    converted_type TEXT,
    converted_id TEXT,
    deleted_at INTEGER
);
CREATE INDEX idx_inbox_status ON inbox_items(status);

CREATE TABLE duration_observations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT,
    estimate_min REAL NOT NULL CHECK (estimate_min > 0),
    actual_min REAL NOT NULL CHECK (actual_min > 0),
    project_id TEXT NOT NULL DEFAULT '',
    category TEXT NOT NULL DEFAULT '',
    observed_at INTEGER NOT NULL
);

CREATE TABLE settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE VIRTUAL TABLE search_index USING fts5(
    kind UNINDEXED, ref_id UNINDEXED, title, body, tokenize = 'unicode61 remove_diacritics 2'
);
"""


SCHEMA_V2 = """
CREATE TABLE reminders (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL CHECK (kind IN ('task','event','focus','planning','closure','alarm','snooze')),
    ref_id TEXT,
    dedupe_key TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL DEFAULT '',
    body TEXT NOT NULL DEFAULT '',
    fire_ts INTEGER NOT NULL,
    offset_min INTEGER,
    local_time TEXT,
    tz TEXT NOT NULL DEFAULT '',
    repeat_rule TEXT,
    sound TEXT NOT NULL DEFAULT '',
    mode TEXT NOT NULL DEFAULT 'reminder' CHECK (mode IN ('reminder','alarm')),
    snooze_min INTEGER,
    enabled INTEGER NOT NULL DEFAULT 1,
    status TEXT NOT NULL DEFAULT 'scheduled' CHECK (status IN ('scheduled','fired','cancelled')),
    attempts INTEGER NOT NULL DEFAULT 0,
    last_fired_ts INTEGER,
    os_scheduled INTEGER NOT NULL DEFAULT 0,
    schedule_error TEXT NOT NULL DEFAULT '',
    platform_id INTEGER NOT NULL UNIQUE,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);
CREATE INDEX idx_reminders_due ON reminders(status, enabled, fire_ts);
CREATE INDEX idx_reminders_ref ON reminders(kind, ref_id);

CREATE TABLE notification_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    reminder_id TEXT,
    kind TEXT NOT NULL,
    title TEXT NOT NULL DEFAULT '',
    body TEXT NOT NULL DEFAULT '',
    mode TEXT NOT NULL DEFAULT 'reminder',
    scheduled_ts INTEGER,
    fired_ts INTEGER NOT NULL,
    outcome TEXT NOT NULL CHECK (outcome IN ('delivered','late','failed','snoozed','dismissed','opened')),
    detail TEXT NOT NULL DEFAULT '',
    acknowledged_ts INTEGER,
    sound TEXT NOT NULL DEFAULT ''
);
CREATE INDEX idx_nlog_fired ON notification_log(fired_ts);
"""


SCHEMA_V3 = """
-- Company: an end-to-end encrypted group chat between people on the same local network (see services/company_service.py).
-- `secret` is the group's join code; it never leaves this device except when the owner chooses to share it.
CREATE TABLE company (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    secret TEXT NOT NULL,
    created_at INTEGER NOT NULL
);
CREATE TABLE company_message (
    id TEXT PRIMARY KEY,
    company_id TEXT NOT NULL REFERENCES company(id) ON DELETE CASCADE,
    sender TEXT NOT NULL,
    body TEXT NOT NULL,
    ts INTEGER NOT NULL,
    mine INTEGER NOT NULL DEFAULT 0,
    created_at INTEGER NOT NULL
);
CREATE INDEX idx_company_message ON company_message(company_id, ts);
"""
