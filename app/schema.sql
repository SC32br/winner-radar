-- Каркас БД радара заказов. Лоты, контакты, документы, кэш организаций.

CREATE TABLE IF NOT EXISTS lots (
    id INTEGER PRIMARY KEY,
    external_id TEXT NOT NULL UNIQUE,
    source TEXT NOT NULL DEFAULT '',
    url TEXT,
    subject TEXT,
    amount_rub INTEGER,
    currency TEXT NOT NULL DEFAULT 'RUB',
    region_code TEXT,
    region_text TEXT,
    published_at TEXT,
    signed_at TEXT,
    fz TEXT,
    okpd_codes TEXT,
    matched_keywords TEXT,
    customer_name TEXT,
    customer_inn TEXT,
    winner_name TEXT,
    winner_inn TEXT,
    winner_status TEXT,
    score REAL,
    profiles TEXT,
    reason TEXT,
    lead_analysis TEXT,
    status TEXT NOT NULL DEFAULT 'new',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS contacts (
    id INTEGER PRIMARY KEY,
    lot_id INTEGER NOT NULL REFERENCES lots(id) ON DELETE CASCADE,
    value TEXT NOT NULL,
    type TEXT NOT NULL,
    party TEXT NOT NULL,
    source TEXT,
    confidence REAL,
    snippet TEXT
);

CREATE TABLE IF NOT EXISTS documents (
    id INTEGER PRIMARY KEY,
    lot_id INTEGER NOT NULL REFERENCES lots(id) ON DELETE CASCADE,
    url TEXT,
    local_path TEXT,
    filename TEXT,
    mime TEXT,
    ocr_status TEXT NOT NULL DEFAULT 'pending',
    ocr_text TEXT,
    ocr_summary TEXT
);

CREATE TABLE IF NOT EXISTS org_cache (
    inn TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT '',
    ogrn TEXT,
    name TEXT,
    status TEXT,
    payload TEXT,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (inn, source)
);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY,
    lot_id INTEGER REFERENCES lots(id) ON DELETE SET NULL,
    external_id TEXT,
    type TEXT NOT NULL,
    payload TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_events_external ON events (external_id);
CREATE INDEX IF NOT EXISTS idx_lots_status ON lots (status);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
