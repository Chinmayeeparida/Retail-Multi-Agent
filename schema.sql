-- =====================================================================
-- Retail multi-agent project: database schema (SQLite)
-- Design rules:
--   * Stores are ROWS, never columns, so a new store is just one new row.
--   * Every fact is stored once; other tables point to it with a FOREIGN KEY.
--   * Dates are stored as text in ISO format: 'YYYY-MM-DD'.
-- =====================================================================

-- ---------- stores: Store A ... Store H (new stores = new rows) ----------
CREATE TABLE stores (
    store_id      INTEGER PRIMARY KEY,
    store_code    TEXT    NOT NULL UNIQUE,                    -- e.g. 'Store A'
    is_active     INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    created_date  TEXT    NOT NULL DEFAULT (date('now'))
);

-- ---------- vendors ----------
CREATE TABLE vendors (
    vendor_id        INTEGER PRIMARY KEY,
    vendor_name      TEXT    NOT NULL UNIQUE COLLATE NOCASE,  -- 'ABC' and 'abc' count as the same
    vendor_gstin     TEXT,                                    -- vendor's GST registration number
    registered_date  TEXT,
    is_india_vendor  INTEGER NOT NULL DEFAULT 1 CHECK (is_india_vendor IN (0, 1)),  -- drives 'IN' in new SKUs
    is_active        INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1))
);

-- ---------- categories: links a category to its SKU prefix (HNL = Home and Living) ----------
CREATE TABLE categories (
    category_id    INTEGER PRIMARY KEY,
    category_name  TEXT NOT NULL UNIQUE,
    sku_prefix     TEXT UNIQUE                                 -- blank for categories with no prefix of their own
);

-- ---------- products: one row per SKU ----------
CREATE TABLE products (
    product_id     INTEGER PRIMARY KEY,                        -- permanent internal ID
    sku            TEXT    NOT NULL UNIQUE,                    -- readable code printed on barcodes
    product_number INTEGER,                                    -- the 6-digit part of the SKU
    product_name   TEXT    NOT NULL,
    vendor_id      INTEGER REFERENCES vendors(vendor_id),
    category_id    INTEGER REFERENCES categories(category_id),
    sub_category   TEXT,
    brand          TEXT,
    hsn_code       TEXT,                                       -- text, so leading zeros are kept
    gst_rate       REAL,                                       -- tax rate in %, e.g. 18
    colour         TEXT,
    is_active      INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    created_date   TEXT    NOT NULL DEFAULT (date('now'))
);

-- ---------- product_prices: price history (a price change = a new row) ----------
CREATE TABLE product_prices (
    price_id        INTEGER PRIMARY KEY,
    product_id      INTEGER NOT NULL REFERENCES products(product_id),
    mrp             REAL,
    cost_price      REAL,
    selling_price   REAL,
    effective_from  TEXT NOT NULL
);

-- ---------- warehouse_stock: what is on the shelves, and where ----------
CREATE TABLE warehouse_stock (
    product_id     INTEGER NOT NULL REFERENCES products(product_id),
    snapshot_date  TEXT    NOT NULL,
    rack_name      TEXT,                                       -- e.g. 'R03-B-07'; empty when no stock
    available_qty  INTEGER NOT NULL DEFAULT 0 CHECK (available_qty >= 0),
    damaged_qty    INTEGER NOT NULL DEFAULT 0 CHECK (damaged_qty >= 0),
    PRIMARY KEY (product_id, snapshot_date)
);

-- ---------- sales: product-level sales per store (returns may be negative) ----------
CREATE TABLE sales (
    sale_id      INTEGER PRIMARY KEY,
    sale_date    TEXT    NOT NULL,
    store_id     INTEGER NOT NULL REFERENCES stores(store_id),
    product_id   INTEGER NOT NULL REFERENCES products(product_id),
    quantity     INTEGER NOT NULL,
    net_amount   REAL
);

-- ---------- restock_requests: what each store asks the warehouse for (SIMULATED for testing) ----------
-- status starts as 'pending'; Agent 1 later sets 'approved', 'out_of_stock' or 'not_needed' (the store already has enough).
CREATE TABLE restock_requests (
    request_id     INTEGER PRIMARY KEY,
    request_date   TEXT    NOT NULL,
    requested_at   TEXT,                                     -- date and time the store raised it (local time)
    store_id       INTEGER NOT NULL REFERENCES stores(store_id),
    product_id     INTEGER NOT NULL REFERENCES products(product_id),
    requested_qty  INTEGER NOT NULL CHECK (requested_qty > 0),
    status         TEXT    NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'approved', 'out_of_stock', 'not_needed')),
    approved_qty   INTEGER CHECK (approved_qty >= 0),       -- filled by Agent 1 (can be less than requested)
    decision_reason TEXT,                                    -- filled by Agent 1, plain words
    requested_by   TEXT,                                     -- name of the store staff who raised it (SIMULATED names)
    request_note   TEXT,                                     -- e.g. a big order the store wants held in the warehouse
    source         TEXT    NOT NULL DEFAULT 'store_request' CHECK (source IN ('store_request', 'vendor_receipt')),
    UNIQUE (request_date, store_id, product_id)          -- a store asks for a product once per day
);

-- ---------- allocation_edits: every time a person changes what Agent 1 proposed (training data for later) ----------
CREATE TABLE allocation_edits (
    edit_id     INTEGER PRIMARY KEY,
    request_id  INTEGER NOT NULL REFERENCES restock_requests(request_id),
    agent_qty   INTEGER NOT NULL,        -- what the agent proposed
    human_qty   INTEGER NOT NULL,        -- what the person decided
    reason      TEXT,
    edited_by   TEXT    NOT NULL DEFAULT 'warehouse manager',
    edited_at   TEXT    NOT NULL DEFAULT (datetime('now', 'localtime'))
);

-- ---------- goods_receipts: stock arriving from a vendor, and how it was shared out ----------
CREATE TABLE goods_receipts (
    receipt_id          INTEGER PRIMARY KEY,
    receipt_date        TEXT    NOT NULL,
    product_id          INTEGER NOT NULL REFERENCES products(product_id),
    vendor_id           INTEGER REFERENCES vendors(vendor_id),
    qty_received        INTEGER NOT NULL CHECK (qty_received > 0),
    distribution_method TEXT    NOT NULL,                              -- equal / top / custom
    qty_sent_to_stores  INTEGER NOT NULL CHECK (qty_sent_to_stores >= 0),   -- the rest stays in the warehouse
    approved_by         TEXT    NOT NULL DEFAULT 'warehouse manager',
    created_at          TEXT    NOT NULL DEFAULT (datetime('now', 'localtime'))
);

-- ---------- barcode_requests: one per store packing list ("print this many labels") ----------
CREATE TABLE barcode_requests (
    barcode_request_id  INTEGER PRIMARY KEY,
    store_id            INTEGER NOT NULL REFERENCES stores(store_id),
    requested_at        TEXT    NOT NULL DEFAULT (datetime('now', 'localtime')),
    total_labels        INTEGER NOT NULL CHECK (total_labels > 0),    -- one label per unit to pack
    status              TEXT    NOT NULL DEFAULT 'requested' CHECK (status IN ('requested', 'printed')),
    printed_at          TEXT
);

-- ---------- packing_items: one row per product line to pack for a store ----------
CREATE TABLE packing_items (
    pack_id             INTEGER PRIMARY KEY,
    barcode_request_id  INTEGER NOT NULL REFERENCES barcode_requests(barcode_request_id),
    request_id          INTEGER NOT NULL UNIQUE REFERENCES restock_requests(request_id),
    store_id            INTEGER NOT NULL REFERENCES stores(store_id),
    product_id          INTEGER NOT NULL REFERENCES products(product_id),
    rack_name           TEXT,
    qty_to_pack         INTEGER NOT NULL CHECK (qty_to_pack > 0),
    packed_qty          INTEGER CHECK (packed_qty >= 0),               -- filled by warehouse staff
    damaged_qty         INTEGER NOT NULL DEFAULT 0 CHECK (damaged_qty >= 0),
    pack_status         TEXT    NOT NULL DEFAULT 'to_pack' CHECK (pack_status IN ('to_pack', 'packed', 'short')),
    staff_note          TEXT,
    packed_at           TEXT,
    trip_id             INTEGER REFERENCES trips(trip_id)              -- set by Agent 2 when the line goes on a trip
);

-- ---------- trip_schedule: which stores the vehicle visits on which weekday (1=Mon ... 5=Fri); editable ----------
CREATE TABLE trip_schedule (
    weekday   INTEGER NOT NULL CHECK (weekday BETWEEN 1 AND 5),
    store_id  INTEGER NOT NULL REFERENCES stores(store_id),
    PRIMARY KEY (weekday, store_id)
);

-- ---------- trips: one delivery run from the warehouse to several stores (created by Agent 2) ----------
CREATE TABLE trips (
    trip_id     INTEGER PRIMARY KEY,
    trip_code   TEXT    NOT NULL UNIQUE,                               -- e.g. TRIP-20250830-001
    trip_date   TEXT    NOT NULL,
    status      TEXT    NOT NULL DEFAULT 'dispatched' CHECK (status IN ('dispatched', 'received', 'closed')),
    created_by  TEXT    NOT NULL DEFAULT 'Agent 2',
    created_at  TEXT    NOT NULL DEFAULT (datetime('now', 'localtime')),
    note        TEXT
);

-- a trip records its OWN list of stores, so adding a store later never disturbs old trips
CREATE TABLE trip_stores (
    trip_id        INTEGER NOT NULL REFERENCES trips(trip_id),
    store_id       INTEGER NOT NULL REFERENCES stores(store_id),
    boxes_count    INTEGER NOT NULL,
    total_units    INTEGER NOT NULL,
    short_lines    INTEGER NOT NULL DEFAULT 0,
    damaged_units  INTEGER NOT NULL DEFAULT 0,
    status         TEXT    NOT NULL DEFAULT 'in_transit' CHECK (status IN ('in_transit', 'reached', 'checking', 'received')),
    reached_at     TEXT,          -- when the store pressed 'Items reached'
    checking_at    TEXT,          -- when the store pressed 'Start checking'
    PRIMARY KEY (trip_id, store_id)
);

CREATE TABLE boxes (
    box_id    INTEGER PRIMARY KEY,
    trip_id   INTEGER NOT NULL REFERENCES trips(trip_id),
    store_id  INTEGER NOT NULL REFERENCES stores(store_id),
    box_code  TEXT    NOT NULL UNIQUE,                                 -- e.g. TRIP-20250830-001-S1-B02
    units     INTEGER NOT NULL CHECK (units > 0),
    status    TEXT    NOT NULL DEFAULT 'packed' CHECK (status IN ('packed', 'received'))
);

CREATE TABLE box_items (
    box_item_id      INTEGER PRIMARY KEY,
    box_id           INTEGER NOT NULL REFERENCES boxes(box_id),
    packing_item_id  INTEGER NOT NULL REFERENCES packing_items(pack_id),
    product_id       INTEGER NOT NULL REFERENCES products(product_id),
    qty              INTEGER NOT NULL CHECK (qty > 0)
);

-- ---------- store receiving (Step 12): store staff check a trip's boxes against the packing list ----------
CREATE TABLE store_receipts (
    receipt_id      INTEGER PRIMARY KEY,
    trip_id         INTEGER NOT NULL REFERENCES trips(trip_id),
    store_id        INTEGER NOT NULL REFERENCES stores(store_id),
    checker_name    TEXT    NOT NULL,
    boxes_expected  INTEGER NOT NULL,
    boxes_received  INTEGER NOT NULL,
    received_at     TEXT    NOT NULL DEFAULT (datetime('now', 'localtime')),
    is_simulated    INTEGER NOT NULL DEFAULT 0,
    UNIQUE (trip_id, store_id)
);

CREATE TABLE receipt_items (
    receipt_item_id  INTEGER PRIMARY KEY,
    receipt_id       INTEGER NOT NULL REFERENCES store_receipts(receipt_id),
    packing_item_id  INTEGER NOT NULL REFERENCES packing_items(pack_id),
    expected_qty     INTEGER NOT NULL,
    received_qty     INTEGER NOT NULL CHECK (received_qty >= 0),   -- units that arrived
    damaged_qty      INTEGER NOT NULL DEFAULT 0 CHECK (damaged_qty >= 0),  -- of those, found damaged
    remark           TEXT    -- worked out by the agent: Good / Extra received / Less
);

-- ---------- trip_reports (Step 13): the manager's performance report for a finished trip ----------
CREATE TABLE trip_reports (
    trip_id         INTEGER PRIMARY KEY REFERENCES trips(trip_id),
    report_text     TEXT NOT NULL,
    summary_source  TEXT NOT NULL CHECK (summary_source IN ('ai', 'standard')),
    created_at      TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

-- ---------- warehouse_alerts: a warning for the warehouse team when a store finds many problem lines ----------
CREATE TABLE warehouse_alerts (
    alert_id       INTEGER PRIMARY KEY,
    trip_id        INTEGER NOT NULL REFERENCES trips(trip_id),
    store_id       INTEGER NOT NULL REFERENCES stores(store_id),
    level          TEXT    NOT NULL CHECK (level IN ('NOTICE', 'WARNING', 'SERIOUS')),
    problem_lines  INTEGER NOT NULL,
    total_lines    INTEGER NOT NULL,
    message        TEXT    NOT NULL,
    acknowledged   INTEGER NOT NULL DEFAULT 0,
    created_at     TEXT    NOT NULL DEFAULT (datetime('now', 'localtime')),
    UNIQUE (trip_id, store_id)
);

-- ---------- store_stock: what each store has on its shelves (SIMULATED - not in the source data) ----------
CREATE TABLE store_stock (
    store_id       INTEGER NOT NULL REFERENCES stores(store_id),
    product_id     INTEGER NOT NULL REFERENCES products(product_id),
    snapshot_date  TEXT    NOT NULL,
    store_qty      INTEGER NOT NULL DEFAULT 0 CHECK (store_qty >= 0),
    PRIMARY KEY (store_id, product_id, snapshot_date)
);

-- ---------- agent_runs: a log of every time an agent ran (who, for which store, what happened) ----------
CREATE TABLE agent_runs (
    run_id              INTEGER PRIMARY KEY,
    agent_name          TEXT    NOT NULL,
    store_id            INTEGER REFERENCES stores(store_id),
    run_time            TEXT    NOT NULL DEFAULT (datetime('now', 'localtime')),
    requests_processed  INTEGER,
    approved_count      INTEGER,
    partial_count       INTEGER,
    out_of_stock_count  INTEGER,
    note                TEXT
);

-- ---------- data_quality_log: a notebook of everything the cleaning script fixed or flagged ----------
CREATE TABLE data_quality_log (
    log_id          INTEGER PRIMARY KEY,
    issue           TEXT    NOT NULL,
    affected_count  INTEGER,
    action_taken    TEXT    NOT NULL,
    logged_at       TEXT    NOT NULL DEFAULT (datetime('now', 'localtime'))
);

-- ---------- id_counters: hands out the next unique number for new SKUs ----------
CREATE TABLE id_counters (
    counter_name  TEXT PRIMARY KEY,
    next_value    INTEGER NOT NULL
);

-- ---------- indexes: make the common look-ups fast ----------
CREATE INDEX idx_sales_store_date ON sales(store_id, sale_date);
CREATE INDEX idx_sales_product    ON sales(product_id);
CREATE INDEX idx_products_vendor  ON products(vendor_id);
CREATE INDEX idx_products_category ON products(category_id);
CREATE INDEX idx_restock_store_date ON restock_requests(store_id, request_date);
CREATE INDEX idx_packing_store ON packing_items(store_id, pack_status);
CREATE INDEX idx_boxes_trip ON boxes(trip_id, store_id);
