-- MejaBahasa SQLite schema (day-1 scaffold).
-- Created at runtime; *.db files are git-ignored (see .gitignore).
-- Order status flow (guardrail: human-in-the-loop):
--   draft -> confirmed (guest) -> acc_pending (staff asked)
--   -> acc_accepted -> sent_to_kitchen -> done
--   acc_pending -> acc_rejected (staff declines; no ticket is sent)

CREATE TABLE IF NOT EXISTS menu_items (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    category TEXT NOT NULL,
    price_idr INTEGER NOT NULL CHECK (price_idr >= 0),
    stock INTEGER NOT NULL DEFAULT 0 CHECK (stock >= 0),
    description TEXT DEFAULT '',
    ingredients_json TEXT NOT NULL DEFAULT '[]',
    allergens_json TEXT NOT NULL DEFAULT '[]',
    spice_level TEXT NOT NULL DEFAULT 'tidak pedas'
        CHECK (spice_level IN ('tidak pedas', 'sedang', 'pedas')),
    is_vegetarian INTEGER NOT NULL DEFAULT 0
        CHECK (is_vegetarian IN (0, 1))
);

CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    table_no TEXT NOT NULL,
    language TEXT NOT NULL CHECK (language IN ('zh', 'ko', 'ja', 'id')),
    status TEXT NOT NULL DEFAULT 'draft'
        CHECK (status IN (
            'draft', 'confirmed', 'acc_pending',
            'acc_accepted', 'acc_rejected', 'sent_to_kitchen', 'done')),
    allergy_notes TEXT NOT NULL DEFAULT '',
    dietary_notes TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS order_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id INTEGER NOT NULL REFERENCES orders(id),
    menu_item_id INTEGER NOT NULL REFERENCES menu_items(id),
    qty INTEGER NOT NULL CHECK (qty > 0),
    note TEXT DEFAULT '',
    custom_note TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS kitchen_tickets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id INTEGER NOT NULL UNIQUE REFERENCES orders(id),
    status TEXT NOT NULL DEFAULT 'open'
        CHECK (status IN ('open', 'cooking', 'ready', 'served')),
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Audit log: EVERY agent tool call is recorded here (input + output).
CREATE TABLE IF NOT EXISTS agent_action_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id INTEGER REFERENCES orders(id),
    action TEXT NOT NULL,
    tool TEXT NOT NULL,
    input_json TEXT DEFAULT '{}',
    output_json TEXT DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_order_items_order ON order_items(order_id);
CREATE INDEX IF NOT EXISTS idx_agent_log_order ON agent_action_log(order_id);
CREATE INDEX IF NOT EXISTS idx_menu_category ON menu_items(category);
