"""
NP Stock Manager — Database layer
PostgreSQL with unified stock positions across Takealot, Home, Website, and Supplier pipeline.
All tables prefixed with np_ to avoid conflicts in shared Postgres.
"""

import os
import psycopg2
from psycopg2.extras import RealDictCursor
from datetime import datetime, timedelta

DATABASE_URL = os.environ.get("DATABASE_URL", "")


def get_db():
    conn = psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)
    conn.autocommit = False
    return conn


def init_db():
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
    CREATE TABLE IF NOT EXISTS np_products (
        id SERIAL PRIMARY KEY,
        sku TEXT UNIQUE NOT NULL,
        title TEXT NOT NULL,
        category TEXT DEFAULT '',
        pack_size_kg REAL DEFAULT 1.0,
        primary_supplier TEXT DEFAULT '',
        supplier_code TEXT DEFAULT '',
        cogs_per_kg REAL DEFAULT 0,
        takealot_price REAL DEFAULT 0,
        website_price REAL DEFAULT 0,
        reorder_point INTEGER DEFAULT 10,
        reorder_qty INTEGER DEFAULT 20,
        min_order_kg REAL DEFAULT 0,
        active INTEGER DEFAULT 1,
        created_at TIMESTAMPTZ DEFAULT NOW(),
        updated_at TIMESTAMPTZ DEFAULT NOW()
    );

    CREATE TABLE IF NOT EXISTS np_stock_positions (
        id SERIAL PRIMARY KEY,
        product_id INTEGER NOT NULL REFERENCES np_products(id),
        location TEXT NOT NULL,
        quantity INTEGER DEFAULT 0,
        updated_at TIMESTAMPTZ DEFAULT NOW(),
        UNIQUE(product_id, location)
    );

    CREATE TABLE IF NOT EXISTS np_stock_counts (
        id SERIAL PRIMARY KEY,
        product_id INTEGER NOT NULL REFERENCES np_products(id),
        location TEXT DEFAULT 'home',
        quantity INTEGER NOT NULL,
        counted_by TEXT DEFAULT 'manual',
        notes TEXT DEFAULT '',
        counted_at TIMESTAMPTZ DEFAULT NOW()
    );

    CREATE TABLE IF NOT EXISTS np_supplier_orders (
        id SERIAL PRIMARY KEY,
        supplier TEXT NOT NULL,
        status TEXT DEFAULT 'ordered',
        order_date TEXT NOT NULL,
        eta_date TEXT DEFAULT '',
        received_date TEXT DEFAULT '',
        total_cost REAL DEFAULT 0,
        notes TEXT DEFAULT '',
        created_at TIMESTAMPTZ DEFAULT NOW(),
        updated_at TIMESTAMPTZ DEFAULT NOW()
    );

    CREATE TABLE IF NOT EXISTS np_supplier_order_items (
        id SERIAL PRIMARY KEY,
        order_id INTEGER NOT NULL REFERENCES np_supplier_orders(id),
        product_id INTEGER REFERENCES np_products(id),
        product_name TEXT NOT NULL,
        quantity_kg REAL NOT NULL,
        quantity_units INTEGER DEFAULT 0,
        cost_per_kg REAL DEFAULT 0,
        line_total REAL DEFAULT 0
    );

    CREATE TABLE IF NOT EXISTS np_sales_velocity (
        id SERIAL PRIMARY KEY,
        product_id INTEGER NOT NULL REFERENCES np_products(id),
        channel TEXT NOT NULL,
        sold_30d INTEGER DEFAULT 0,
        sold_7d INTEGER DEFAULT 0,
        avg_daily REAL DEFAULT 0,
        revenue_30d REAL DEFAULT 0,
        updated_at TIMESTAMPTZ DEFAULT NOW(),
        UNIQUE(product_id, channel)
    );

    CREATE TABLE IF NOT EXISTS np_sync_log (
        id SERIAL PRIMARY KEY,
        source TEXT NOT NULL,
        status TEXT NOT NULL,
        message TEXT DEFAULT '',
        synced_at TIMESTAMPTZ DEFAULT NOW()
    );
    """)
    conn.commit()
    conn.close()


def seed_products_from_takealot(offers):
    """Seed/update products table from Takealot offer data."""
    conn = get_db()
    cur = conn.cursor()
    for o in offers:
        sku = o.get("sku", "")
        if not sku:
            continue
        title = o.get("title", "Unknown")
        price = o.get("selling_price", 0)
        cur.execute("""
            INSERT INTO np_products (sku, title, takealot_price)
            VALUES (%s, %s, %s)
            ON CONFLICT(sku) DO UPDATE SET
                title = EXCLUDED.title,
                takealot_price = EXCLUDED.takealot_price,
                updated_at = NOW()
        """, (sku, title, price))
    conn.commit()
    conn.close()


def update_takealot_stock(offer_data):
    """Update stock positions from Takealot API data."""
    conn = get_db()
    cur = conn.cursor()
    for o in offer_data:
        sku = o.get("sku", "")
        if not sku:
            continue

        cur.execute("SELECT id FROM np_products WHERE sku = %s", (sku,))
        row = cur.fetchone()
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
            cur.execute("""
                INSERT INTO np_stock_positions (product_id, location, quantity, updated_at)
                VALUES (%s, %s, %s, NOW())
                ON CONFLICT(product_id, location) DO UPDATE SET
                    quantity = EXCLUDED.quantity,
                    updated_at = NOW()
            """, (pid, loc, qty))

        # Sales velocity
        total_sold = sum(r.get("quantity_sold_30_days", 0) for r in wh)
        total_rev = total_sold * o.get("selling_price", 0)
        cur.execute("""
            INSERT INTO np_sales_velocity (product_id, channel, sold_30d, avg_daily, revenue_30d, updated_at)
            VALUES (%s, 'takealot', %s, %s, %s, NOW())
            ON CONFLICT(product_id, channel) DO UPDATE SET
                sold_30d = EXCLUDED.sold_30d,
                avg_daily = EXCLUDED.avg_daily,
                revenue_30d = EXCLUDED.revenue_30d,
                updated_at = NOW()
        """, (pid, total_sold, round(total_sold / 30, 2), total_rev))

    conn.commit()
    conn.close()


def get_unified_stock():
    """Get unified stock view across all locations."""
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
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
            COALESCE(sp_home.updated_at::text, '') as home_updated,
            COALESCE((
                SELECT SUM(soi.quantity_units)
                FROM np_supplier_order_items soi
                JOIN np_supplier_orders so ON soi.order_id = so.id
                WHERE soi.product_id = p.id AND so.status IN ('ordered', 'in_transit')
            ), 0) as pipeline_stock
        FROM np_products p
        LEFT JOIN np_stock_positions sp_home ON p.id = sp_home.product_id AND sp_home.location = 'home'
        LEFT JOIN np_stock_positions sp_cpt ON p.id = sp_cpt.product_id AND sp_cpt.location = 'takealot_cpt'
        LEFT JOIN np_stock_positions sp_jnb ON p.id = sp_jnb.product_id AND sp_jnb.location = 'takealot_jnb'
        LEFT JOIN np_stock_positions sp_dbn ON p.id = sp_dbn.product_id AND sp_dbn.location = 'takealot_dbn'
        LEFT JOIN np_stock_positions sp_web ON p.id = sp_web.product_id AND sp_web.location = 'website'
        LEFT JOIN np_sales_velocity sv ON p.id = sv.product_id AND sv.channel = 'takealot'
        WHERE p.active = 1
        ORDER BY COALESCE(sv.revenue_30d, 0) DESC
    """)
    rows = cur.fetchall()
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

        if days_cover < 14 and item["sold_30d"] > 0:
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
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO np_stock_counts (product_id, location, quantity, notes)
        VALUES (%s, %s, %s, %s)
    """, (product_id, location, quantity, notes))

    cur.execute("""
        INSERT INTO np_stock_positions (product_id, location, quantity, updated_at)
        VALUES (%s, %s, %s, NOW())
        ON CONFLICT(product_id, location) DO UPDATE SET
            quantity = EXCLUDED.quantity,
            updated_at = NOW()
    """, (product_id, location, quantity))

    conn.commit()
    conn.close()


def get_supplier_orders(status_filter=None):
    """Get supplier orders with their items."""
    conn = get_db()
    cur = conn.cursor()
    if status_filter:
        cur.execute(
            "SELECT * FROM np_supplier_orders WHERE status = %s ORDER BY order_date DESC",
            (status_filter,)
        )
    else:
        cur.execute("SELECT * FROM np_supplier_orders ORDER BY order_date DESC")
    orders = cur.fetchall()

    result = []
    for o in orders:
        cur.execute(
            "SELECT * FROM np_supplier_order_items WHERE order_id = %s", (o["id"],)
        )
        items = cur.fetchall()
        result.append({**dict(o), "items": [dict(i) for i in items]})

    conn.close()
    return result


def create_supplier_order(supplier, order_date, eta_date, total_cost, notes, items):
    """Create a new supplier order with line items."""
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO np_supplier_orders (supplier, order_date, eta_date, total_cost, notes)
        VALUES (%s, %s, %s, %s, %s)
        RETURNING id
    """, (supplier, order_date, eta_date, total_cost, notes))
    order_id = cur.fetchone()["id"]

    for item in items:
        cur.execute("""
            INSERT INTO np_supplier_order_items
                (order_id, product_id, product_name, quantity_kg, quantity_units, cost_per_kg, line_total)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
        """, (order_id, item.get("product_id"), item["product_name"],
              item["quantity_kg"], item.get("quantity_units", 0),
              item.get("cost_per_kg", 0), item.get("line_total", 0)))

    conn.commit()
    conn.close()
    return order_id


def update_order_status(order_id, status):
    """Update supplier order status."""
    conn = get_db()
    cur = conn.cursor()
    now = datetime.now().isoformat()
    received_date = datetime.now().strftime("%Y-%m-%d") if status == "received" else None
    cur.execute(
        "UPDATE np_supplier_orders SET status = %s, updated_at = %s, received_date = COALESCE(%s, received_date) WHERE id = %s",
        (status, now, received_date, order_id)
    )
    conn.commit()
    conn.close()


def get_all_products():
    """Get all active products."""
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM np_products WHERE active = 1 ORDER BY title")
    rows = cur.fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_stock_count_history(limit=50):
    """Get recent stock count history."""
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        SELECT sc.*, p.sku, p.title
        FROM np_stock_counts sc
        JOIN np_products p ON sc.product_id = p.id
        ORDER BY sc.counted_at DESC
        LIMIT %s
    """, (limit,))
    rows = cur.fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_sync_status():
    """Get last sync times for each source."""
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        SELECT source, MAX(synced_at) as last_sync, status
        FROM np_sync_log
        GROUP BY source, status
        ORDER BY MAX(synced_at) DESC
    """)
    rows = cur.fetchall()
    conn.close()
    return [dict(r) for r in rows]


def log_sync(source, status, message=""):
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO np_sync_log (source, status, message) VALUES (%s, %s, %s)",
        (source, status, message)
    )
    conn.commit()
    conn.close()


def update_product_supplier_info(sku, supplier, cogs_per_kg, supplier_code=""):
    """Update supplier and COGS info for a product."""
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        UPDATE np_products SET
            primary_supplier = %s,
            cogs_per_kg = %s,
            supplier_code = %s,
            updated_at = NOW()
        WHERE sku = %s
    """, (supplier, cogs_per_kg, supplier_code, sku))
    conn.commit()
    conn.close()
