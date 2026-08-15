-- ================================================================
-- KINOMOTOR — schema.sql
-- SQLite. Три таблицы: users, login_codes, generations.
-- Деньги храним в копейках (integer), чтобы не ловить ошибки float.
-- ================================================================

CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    email         TEXT NOT NULL UNIQUE,
    balance_kop   INTEGER NOT NULL DEFAULT 0,
    created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS login_codes (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    email         TEXT NOT NULL,
    code          TEXT NOT NULL,
    expires_at    TEXT NOT NULL,
    used          INTEGER NOT NULL DEFAULT 0,
    created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS generations (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         INTEGER NOT NULL REFERENCES users(id),
    topic           TEXT NOT NULL,
    source          TEXT NOT NULL,
    duration        INTEGER NOT NULL,
    price_kop       INTEGER NOT NULL,
    status          TEXT NOT NULL DEFAULT 'pending',
    video_path      TEXT,
    social_description TEXT,
    hashtags        TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    expires_at      TEXT,
    error_message   TEXT
);

CREATE INDEX IF NOT EXISTS idx_login_codes_email ON login_codes(email);
CREATE INDEX IF NOT EXISTS idx_generations_user ON generations(user_id);

CREATE TABLE IF NOT EXISTS sessions (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    token         TEXT NOT NULL UNIQUE,
    user_id       INTEGER NOT NULL REFERENCES users(id),
    expires_at    TEXT NOT NULL,
    created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_sessions_token ON sessions(token);

CREATE TABLE IF NOT EXISTS payments (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id       INTEGER NOT NULL REFERENCES users(id),
    order_id      TEXT NOT NULL UNIQUE,
    amount_kop    INTEGER NOT NULL,
    status        TEXT NOT NULL DEFAULT 'NEW',
    payment_id    TEXT,
    created_at    TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at    TEXT
);

CREATE INDEX IF NOT EXISTS idx_payments_order ON payments(order_id);

CREATE TABLE IF NOT EXISTS support_tickets (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id       INTEGER NOT NULL REFERENCES users(id),
    subject       TEXT NOT NULL,
    status        TEXT NOT NULL DEFAULT 'open',
    created_at    TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS support_messages (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    ticket_id     INTEGER NOT NULL REFERENCES support_tickets(id),
    sender        TEXT NOT NULL,
    message       TEXT NOT NULL,
    created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_support_tickets_user ON support_tickets(user_id);
CREATE INDEX IF NOT EXISTS idx_support_messages_ticket ON support_messages(ticket_id);
