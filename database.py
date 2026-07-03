"""
NP Stock Manager — Database layer
SQLite with unified stock positions across Takealot, Home, Website, and Supplier pipeline.
"""

import sqlite3
import os
from datetime import datetime, timedelta
from pathlib import Path

DB_PATH = os.environ.get("DB_PATH", "np_stock.db")


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db():
    conn = get_db()
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS products (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        sku TEXT UNIQUE NOT NULL,
        title TEXT NOT NULL,
        category TEXT DEFAULT '',
        pack_size_kg REAL DEFAULT 1.0,
        -- Supplier info
        primary_supplier TEXT DEFAULT '',
        supplier_code TEXT DEFAULT '',
        cogs_per_kg REAL DEFAULT 0,
        -- Pricing
        takealot_price REAL DEFAULT 0,
        website_price REAL DEFAULT 0,
        -- Thresholds
        reorder_point INTEGER DEFAULT 10,
        reorder_qty INTEGER DEFAULT 20,
        min_order_kg REAL DEFAULT 0,
        -- Metadata
        active INTEGER DEFAULT 1,
        created_at TEXT DEFAULT (datetime('now')),
        updated_at TEXT DEFAULT (datetime('now'))
    );

    CREATE TABLE IF NOT EXISTS stock_positions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        product_id INTEGER NOT NULL REFERENCES products(id),
        location TEXT NOT NULL,  -- 'home', 'takealot_cpt', 'takealot_jnb', 'takealot_dbn', 'website'
        quantity INTEGER DEFAULT 0,
        updated_at TEXT DEFAULT (datetime('now')),
        UNIQUE(product_id, location)
    );

    CREATE TABLE IF NOT EXISTS stock_counts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        product_id INTEGER NOT NULL REFERENCES products(id),
        location TEXT DEFAULT 'home',
        quantity INTEGER NOT NULL,
        counted_by TEXT DEFAULT 'manual',
        notes TEXT DEFAULT '',
        counted_at TEXT DEFAULT (datetime('now'))
    );

    CREATE TABLE IF NOT EXISTS supplier_orders (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        supplier TEXT NOT NULL,
        status TEXT DEFAULT 'ordered',  -- ordered, in_transit, received, allocated
        order_date TEXT NOT NULL,
        eta_date TEXT DEFAULT '',
        received_date TEXT DEFAULT '',
        total_cost REAL DEFAULT 0,
        notes TEXT DEFAULT '',
        created_at TEXT DEFAULT (datetime('now')),
        updated_at TEXT DEFAULT (datetime('now'))
    );

    CREATE TABLE IF NOT EXISTS supplier_order_items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        order_id INTEGER NOT NULL REFERENCES supplier_orders(id),
        product_id INTEGER REFERENCES products(id),
        product_name TEXT NOT NULL,
        quantity_kg REAL NOT NULL,
        quantity_units INTEGER DEFAULT 0,
        cost_per_kg REAL DEFAULT 0,
        line_total REAL DEFAULT 0
    );

    CREATE TABLE IF NOT EXISTS sales_velocity (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        product_id INTEGER NOT NULL REFERENCES products(id),
        channel TEXT NOT NULL,  -- 'takealot', 'website'
        sold_30d INTEGER DEFAULT 0,
        sold_7d INTEGER DEFAULT 0,
        avg_daily REAL DEFAULT 0,
        revenue_30d REAL DEFAULT 0,
        updated_at TEXT DEFAULT (datetime('now')),
        UNIQUE(product_id, channel)
    );

    CREATE TABLE IF NOT EXISTS sync_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        source TEXT NOT NULL,
        status TEXT NOT NULL,
        message TEXT DEFAULT '',
        synced_at TEXT DEFAULT (datetime('now'))
    );
    """)
    conn.commit()
    conn.close()


def seed_products_from_takealot(offers):
    """Seed/update products table from Takealot offer data."""
    conn = get_db()
    for o in offers:
        sku = o.get("sku", "")
        if not sku:
            continue
        title = o.get("title", "Unknown")
        price = o.get("selling_price", 0)
        conn.execute("""
            INSERT INTO products (sku, title, takealot_price)
            VALUES (?, ?, ?)
            ON CONFLICT(sku) DO UPDATE SET
                title = excluded.title,
                takealot_price = excluded.takealot_price,
                updated_at = datetime('now')
        """, (sku, title, price))
    conn.commit()
    conn.close()


def update_takealot_stock(offer_data):
    """Update stock positions from Takealot API data."""
    conn = get_db()
    for o in offer_data:
        sku = o.get("sku", "")
        if not sku:
            continue

        row = conn.execute("SELECT id FROM products WHERE sku = ?", (sku,)).fetchone()
        if not row:
            continue
        pid = row["id"]

        wh = o.get("takealot_warehouse_stock", [])
        region_map = {"CPT": "takealot_cpt", "JNB": "takealot_jnb", "DBN": "takealot_dbn"}
        for r in wh:
            region_code = r.get("region", "")
            loc = region_map.get(region_code)
            if not loc:
                continue
            qty = r.get("quantity_available", 0)
            conn.execute("""
                INSERT INTO stock_positions (product_id, location, quantity, updated_at)
                VALUES (?, ?, ?, datetime('now'))
                ON CONFLICT(product_id, location) DO UPDATE SET
                    quantity = excluded.quantity,
                    updated_at = datetime('now')
            """, (pid, loc, qty))

        # Sales velocity
        total_sold = sum(r.get("quantity_sold_30_days", 0) for r in wh)
        total_rev = total_sold * o.get("selling_price", 0)
        conn.execute("""
            INSERT INTO sales_velocity (product_id, channel, sold_30d, avg_daily, revenue_30d, updated_at)
            VALUES (?, 'takealot', ?, ?, ?, datetime('now'))
            ON CONFLICT(product_id, channel) DO UPDATE SET
                sold_30d = excluded.sold_30d,
                avg_daily = excluded.avg_daily,
                revenue_30d = excluded.revenue_30d,
                updated_at = datetime('now')
        """, (pid, total_sold, round(total_sold / 30, 2), total_rev))

    conn.commit()
    conn.close()


def get_unified_stock():
    """Get unified stock view across all locations."""
    conn = get_db()
    rows = conn.execute("""
        SELECT
            p.id, p.sku, p.title, p.category, p.pack_size_kg,
            p.primary_supplier, p.cogs_per_kg, p.takealot_price, p.website_price,
            p.reorder_point, p.reorder_qty, p.active,
            COALESCE(sp_home.quantity, 0) as home_stock,
            COALESCE(sp_cpt.quantity, 0) as tl_cpt,
            COALESCE(sp_jnb.quantity, 0) as tl_jnb,
            COALESCE(sp_dbn.quantity, 0) as tl_dbn,
            COALESCE(sp_web.quantity, 0) as web_stock,
            COALESCE(sv.sold_30d, 0) as sold_30d,
            COALESCE(sv.avg_daily, 0) as avg_daily,
            COALESCE(sv.revenue_30d, 0) as revenue_30d,
            COALESCE(sp_home.updated_at, '') as home_updated,
            -- Pipeline stock (from open supplier orders)
            COALESCE((
                SELECT SUM(soi.quantity_units)
                FROM supplier_order_items soi
                JOIN supplier_orders so ON soi.order_id = so.id
                WHERE soi.product_id = p.id AND so.status IN ('ordered', 'in_transit')
            ), 0) as pipeline_stock
        FROM products p
        LEFT JOIN stock_positions sp_home ON p.id = sp_home.product_id AND sp_home.location = 'home'
        LEFT JOIN stock_positions sp_cpt ON p.id = sp_cpt.product_id AND sp_cpt.location = 'takealot_cpt'
        LEFT JOIN stock_positions sp_jnb ON p.id = sp_jnb.product_id AND sp_jnb.location = 'takealot_jnb'
        LEFT JOIN stock_positions sp_dbn ON p.id = sp_dbn.product_id AND sp_dbn.location = 'takealot_dbn'
        LEFT JOIN stock_positions sp_web ON p.id = sp_web.product_id AND sp_web.location = 'website'
        LEFT JOIN sales_velocity sv ON p.id = sv.product_id AND sv.channel = 'takealot'
        WHERE p.active = 1
        ORDER BY COALESCE(sv.revenue_30d, 0) DESC
    """).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_reorder_suggestions():
    """Calculate what needs ordering based on velocity and stock levels."""
    stock = get_unified_stock()
    suggestions = []

    for item in stock:
        total_tl = item["tl_cpt"] + item["tl_jnb"] + item["tl_dbn"]
        total_available = total_tl + item["home_stock"] + item["pipeline_stock"]
        daily = item["avg_daily"]

        if daily <= 0:
            days_cover = 999 if total_available > 0 else 0
        else:
            days_cover = round(total_available / daily)

        # Suggest reorder if less than 14 days cover and selling
        if days_cover < 14 and item["sold_30d"] > 0:
            # Target 30 days of cover
            target_qty = max(int(daily * 30) - total_available, 0)
            if target_qty > 0:
                suggestions.append({
                    **item,
                    "total_tl": total_tl,
                    "total_available": total_available,
                    "days_cover": days_cover,
                    "suggested_qty": target_qty,
                    "est_cost": round(target_qty * item["cogs_per_kg"] * item["pack_size_kg"], 2) if item["cogs_per_kg"] > 0 else 0,
                    "urgency": "CRITICAL" if days_cover == 0 else "URGENT" if days_cover < 7 else "SOON",
                })

    suggestions.sort(key=lambda x: (0 if x["urgency"] == "CRITICAL" else 1 if x["urgency"] == "URGENT" else 2, -x["revenue_30d"]))
    return suggestions


def record_stock_count(product_id, quantity, location="home", notes=""):
    """Record a physical stock count and update position."""
    conn = get_db()
    conn.execute("""
        INSERT INTO stock_counts (product_id, location, quantity, notes)
        VALUES (?, ?, ?, ?)
    """, (product_id, location, quantity, notes))

    conn.execute("""
        INSERT INTO stock_positions (product_id, location, quantity, updated_at)
        VALUES (?, ?, ?, datetime('now'))
        ON CONFLICT(product_id, location) DO UPDATE SET
            quantity = excluded.quantity,
            updated_at = datetime('now')
    """, (product_id, location, quantity))

    conn.commit()
    conn.close()


def get_supplier_orders(status_filter=None):
    """Get supplier orders with their items."""
    conn = get_db()
    if status_filter:
        orders = conn.execute(
            "SELECT * FROM supplier_orders WHERE status = ? ORDER BY order_date DESC",
            (status_filter,)
        ).fetchall()
    else:
        orders = conn.execute(
            "SELECT * FROM supplier_orders ORDER BY order_date DESC"
        ).fetchall()

    result = []
    for o in orders:
        items = conn.execute(
            "SELECT * FROM supplier_order_items WHERE order_id = ?", (o["id"],)
        ).fetchall()
        result.append({**dict(o), "items": [dict(i) for i in items]})

    conn.close()
    return result


def create_supplier_order(supplier, order_date, eta_date, total_cost, notes, items):
    """Create a new supplier order with line items."""
    conn = get_db()
    cur = conn.execute("""
        INSERT INTO supplier_orders (supplier, order_date, eta_date, total_cost, notes)
        VALUES (?, ?, ?, ?, ?)
    """, (supplier, order_date, eta_date, total_cost, notes))
    order_id = cur.lastrowid

    for item in items:
        conn.execute("""
            INSERT INTO supplier_order_items
                (order_id, product_id, product_name, quantity_kg, quantity_units, cost_per_kg, line_total)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (order_id, item.get("product_id"), item["product_name"],
              item["quantity_kg"], item.get("quantity_units", 0),
              item.get("cost_per_kg", 0), item.get("line_total", 0)))

    conn.commit()
    conn.close()
    return order_id


def update_order_status(order_id, status):
    """Update supplier order status."""
    conn = get_db()
    updates = {"status": status, "updated_at": datetime.now().isoformat()}
    if status == "received":
        updates["received_date"] = datetime.now().strftime("%Y-%m-%d")
    conn.execute(
        f"UPDATE supplier_orders SET status = ?, updated_at = ?, received_date = COALESCE(?, received_date) WHERE id = ?",
        (status, updates["updated_at"], updates.get("received_date"), order_id)
    )
    conn.commit()
    conn.close()


def get_all_products():
    """Get all active products."""
    conn = get_db()
    rows = conn.execute("SELECT * FROM products WHERE active = 1 ORDER BY title").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_stock_count_history(limit=50):
    """Get recent stock count history."""
    conn = get_db()
    rows = conn.execute("""
        SELECT sc.*, p.sku, p.title
        FROM stock_counts sc
        JOIN products p ON sc.product_id = p.id
        ORDER BY sc.counted_at DESC
        LIMIT ?
    """, (limit,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_sync_status():
    """Get last sync times for each source."""
    conn = get_db()
    rows = conn.execute("""
        SELECT source, MAX(synced_at) as last_sync, status
        FROM sync_log
        GROUP BY source
        ORDER BY last_sync DESC
    """).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def log_sync(source, status, message=""):
    conn = get_db()
    conn.execute(
        "INSERT INTO sync_log (source, status, message) VALUES (?, ?, ?)",
        (source, status, message)
    )
    conn.commit()
    conn.close()


def update_product_supplier_info(sku, supplier, cogs_per_kg, supplier_code=""):
    """Update supplier and COGS info for a product."""
    conn = get_db()
    conn.execute("""
        UPDATE products SET
            primary_supplier = ?,
            cogs_per_kg = ?,
            supplier_code = ?,
            updated_at = datetime('now')
        WHERE sku = ?
    """, (supplier, cogs_per_kg, supplier_code, sku))
    conn.commit()
    conn.close()
