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

-- ================================================================
-- Тариф "Кадры": человек сперва получает картинки, смотрит на них,
-- и только потом отправляет их в видео.
--
-- Между этими двумя шагами человек уходит думать: закрывает вкладку,
-- возвращается через час, сервис за это время может перезапуститься.
-- Поэтому черновик обязан лежать в базе, а не в памяти процесса, как
-- обычная генерация в TASKS.
-- ================================================================

CREATE TABLE IF NOT EXISTS frame_drafts (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id       INTEGER NOT NULL REFERENCES users(id),
    topic         TEXT NOT NULL DEFAULT '',
    source        TEXT NOT NULL,              -- 'generate' (рисует ИИ) | 'upload' (свои фото)
    duration      INTEGER NOT NULL,
    language      TEXT NOT NULL DEFAULT 'ru',
    frames_count  INTEGER NOT NULL,
    price_kop     INTEGER NOT NULL,           -- списано за этап картинок
    status        TEXT NOT NULL DEFAULT 'pending',  -- pending | ready | error
    -- Сценарий целиком (hook_text, voice_text, social_description, hashtags).
    -- Пригодится на втором шаге, чтобы не платить за него повторно.
    script_json   TEXT,
    error_message TEXT,
    created_at    TEXT NOT NULL DEFAULT (datetime('now')),
    expires_at    TEXT
);

CREATE TABLE IF NOT EXISTS frame_images (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    draft_id      INTEGER NOT NULL REFERENCES frame_drafts(id),
    position      INTEGER NOT NULL,
    image_path    TEXT NOT NULL,
    -- Для сгенерированных — промпт сцены. Для загруженных фото — то,
    -- что Gemini увидел на снимке. И то и другое понадобится, чтобы
    -- объяснить Veo, что должно ожить в кадре.
    prompt        TEXT NOT NULL DEFAULT '',
    created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_frame_drafts_user ON frame_drafts(user_id);
CREATE INDEX IF NOT EXISTS idx_frame_images_draft ON frame_images(draft_id);

-- Второй шаг тарифа "Кадры": утверждённые кадры отправлены в видео.
--
-- Сам ролик пишется в общую таблицу generations (source = 'frames') —
-- тогда он сам собой попадает в историю личного кабинета, в админку и
-- под существующую автоочистку через 24 часа. Здесь хранится только
-- связь с черновиком и состояние рендера.
--
-- UNIQUE на draft_id — защита от двойной оплаты: сколько бы раз ни нажали
-- кнопку, второй ряд просто не вставится.
CREATE TABLE IF NOT EXISTS frame_videos (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    draft_id       INTEGER NOT NULL UNIQUE REFERENCES frame_drafts(id),
    user_id        INTEGER NOT NULL REFERENCES users(id),
    generation_id  INTEGER REFERENCES generations(id),
    price_kop      INTEGER NOT NULL,
    status         TEXT NOT NULL DEFAULT 'pending',  -- pending | done | error
    step           INTEGER NOT NULL DEFAULT 0,
    -- Громкости дорожек: {"voice": 100, "music": 35, "veo": 0}.
    -- Ролик хранится разобранным на части, поэтому громкости можно менять
    -- и пересобирать сколько угодно — платить заново не за что.
    mix_json       TEXT,
    error_message  TEXT,
    created_at     TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_frame_videos_draft ON frame_videos(draft_id);
