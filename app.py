"""
NP Stock Manager — Main FastAPI app
Unified stock tracking across Takealot, Home, Website, and Supplier pipeline.
Mobile-first dashboard with stock count form, reorder engine, and order tracker.
"""

import os
from datetime import datetime
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
import uvicorn

from database import (
    init_db, get_unified_stock, get_reorder_suggestions, record_stock_count,
    get_supplier_orders, create_supplier_order, update_order_status,
    get_all_products, get_stock_count_history, get_sync_status, get_db
)
from takealot_sync import sync_takealot


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield

app = FastAPI(title="NP Stock Manager", lifespan=lifespan)

# ── SHARED CSS ──────────────────────────────────────────────────────────────
CSS = """
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  :root {
    --bg: #0A0A0A; --card: #111111; --inset: #1A1A1A;
    --gold: #C5A55A; --gold-light: #D4B96E; --gold-dark: #A8893E;
    --cream: #F5F0E8; --muted: #B8B0A0; --white: #FAFAF8;
    --red: #E74C3C; --orange: #F39C12; --green: #27AE60; --blue: #3498DB;
  }
  body { background: var(--bg); color: var(--cream); font-family: 'DM Sans', -apple-system, sans-serif; font-size: 14px; }
  a { color: var(--gold); text-decoration: none; }
  a:hover { color: var(--gold-light); }

  .container { max-width: 900px; margin: 0 auto; padding: 12px; }
  .nav { display: flex; gap: 6px; padding: 10px 0; overflow-x: auto; white-space: nowrap; border-bottom: 1px solid #222; margin-bottom: 16px; }
  .nav a { padding: 8px 14px; border-radius: 8px; background: var(--card); color: var(--muted); font-size: 13px; font-weight: 500; }
  .nav a.active, .nav a:hover { background: var(--gold); color: #0A0A0A; }

  .card { background: var(--card); border-radius: 10px; padding: 14px; margin-bottom: 12px; border: 1px solid #1e1e1e; }
  .card-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px; }
  .card h3 { font-family: 'Playfair Display', serif; color: var(--cream); font-size: 16px; }

  .stat-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); gap: 8px; margin-bottom: 16px; }
  .stat { background: var(--card); border-radius: 10px; padding: 14px; text-align: center; border: 1px solid #1e1e1e; }
  .stat .value { font-size: 24px; font-weight: 700; font-family: 'Playfair Display', serif; }
  .stat .label { font-size: 11px; color: var(--muted); margin-top: 2px; text-transform: uppercase; letter-spacing: 0.5px; }

  .badge { display: inline-block; padding: 2px 8px; border-radius: 12px; font-size: 11px; font-weight: 600; }
  .badge-critical { background: var(--red); color: white; }
  .badge-urgent { background: var(--orange); color: #111; }
  .badge-soon { background: var(--blue); color: white; }
  .badge-ok { background: var(--green); color: white; }
  .badge-ordered { background: var(--blue); color: white; }
  .badge-in_transit { background: var(--orange); color: #111; }
  .badge-received { background: var(--green); color: white; }
  .badge-allocated { background: var(--gold); color: #111; }

  table { width: 100%; border-collapse: collapse; font-size: 13px; }
  th { text-align: left; color: var(--muted); font-size: 11px; text-transform: uppercase; padding: 8px 6px; border-bottom: 1px solid #222; }
  td { padding: 8px 6px; border-bottom: 1px solid #1a1a1a; }
  tr:hover { background: var(--inset); }

  .form-group { margin-bottom: 14px; }
  .form-group label { display: block; color: var(--muted); font-size: 12px; margin-bottom: 4px; text-transform: uppercase; }
  .form-group input, .form-group select, .form-group textarea {
    width: 100%; padding: 12px; background: var(--inset); border: 1px solid #333;
    border-radius: 8px; color: var(--cream); font-size: 16px; font-family: inherit;
  }
  .form-group select { appearance: none; background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='12' height='12' viewBox='0 0 12 12'%3E%3Cpath fill='%23B8B0A0' d='M6 8L1 3h10z'/%3E%3C/svg%3E"); background-repeat: no-repeat; background-position: right 12px center; }
  .form-group input:focus, .form-group select:focus { border-color: var(--gold); outline: none; }

  .btn { display: inline-block; padding: 12px 24px; border-radius: 8px; font-weight: 600; font-size: 14px; cursor: pointer; border: none; font-family: inherit; }
  .btn-primary { background: var(--gold); color: #0A0A0A; }
  .btn-primary:hover { background: var(--gold-light); }
  .btn-secondary { background: transparent; border: 1px solid var(--gold); color: var(--gold); }
  .btn-sm { padding: 6px 12px; font-size: 12px; }
  .btn-danger { background: var(--red); color: white; }

  .empty { text-align: center; padding: 40px; color: var(--muted); }
  .text-gold { color: var(--gold); }
  .text-red { color: var(--red); }
  .text-green { color: var(--green); }
  .text-muted { color: var(--muted); }
  .text-right { text-align: right; }
  .text-center { text-align: center; }
  .mt-2 { margin-top: 8px; }
  .mb-2 { margin-bottom: 8px; }
  .flex { display: flex; }
  .gap-2 { gap: 8px; }
  .items-center { align-items: center; }
  .justify-between { justify-content: space-between; }

  .scroll-x { overflow-x: auto; -webkit-overflow-scrolling: touch; }

  .header { padding: 16px 0 8px; }
  .header h1 { font-family: 'Playfair Display', serif; color: var(--gold); font-size: 22px; }
  .header p { color: var(--muted); font-size: 12px; }

  .quick-count { display: grid; grid-template-columns: 1fr auto auto; gap: 8px; align-items: center; padding: 10px 0; border-bottom: 1px solid #1a1a1a; }
  .quick-count .name { font-size: 13px; }
  .quick-count input { width: 70px; text-align: center; }
  .quick-count .btn { padding: 8px 12px; }

  @media (max-width: 600px) {
    .stat-grid { grid-template-columns: repeat(2, 1fr); }
    .hide-mobile { display: none; }
    td, th { padding: 6px 4px; font-size: 12px; }
  }
</style>
<link href="https://fonts.googleapis.com/css2?family=Playfair+Display:wght@400;700&family=DM+Sans:wght@400;500;600;700&display=swap" rel="stylesheet">
"""

NAV_TEMPLATE = """
<div class="nav">
  <a href="/" class="{d_active}">Dashboard</a>
  <a href="/stocktake" class="{s_active}">Stock Count</a>
  <a href="/orders" class="{o_active}">Orders</a>
  <a href="/reorder" class="{r_active}">Reorder</a>
  <a href="/products" class="{p_active}">Products</a>
</div>
"""

def nav(active="dashboard"):
    keys = {"d_active": "", "s_active": "", "o_active": "", "r_active": "", "p_active": ""}
    key_map = {"dashboard": "d_active", "stocktake": "s_active", "orders": "o_active", "reorder": "r_active", "products": "p_active"}
    if active in key_map:
        keys[key_map[active]] = "active"
    return NAV_TEMPLATE.format(**keys)

def page(title, content, active="dashboard"):
    return f"""<!DOCTYPE html><html lang="en"><head>
    <meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{title} — NP Stock Manager</title>{CSS}
    </head><body><div class="container">
    <div class="header"><h1>NP Stock Manager</h1><p>Nature's Pleasures — Unified Inventory</p></div>
    {nav(active)}{content}
    </div></body></html>"""


# ── DASHBOARD ───────────────────────────────────────────────────────────────
@app.get("/", response_class=HTMLResponse)
async def dashboard():
    stock = get_unified_stock()
    syncs = get_sync_status()

    total_home = sum(s["home_stock"] for s in stock)
    total_tl = sum(s["tl_cpt"] + s["tl_jnb"] + s["tl_dbn"] for s in stock)
    total_web = sum(s["web_stock"] for s in stock)
    total_pipeline = sum(s["pipeline_stock"] for s in stock)
    total_rev = sum(s["revenue_30d"] for s in stock)
    zero_stock = sum(1 for s in stock if (s["tl_cpt"] + s["tl_jnb"] + s["tl_dbn"]) == 0 and s["sold_30d"] > 0)

    last_tl_sync = next((s["last_sync"] for s in syncs if s["source"] == "takealot"), "Never")
    last_count = next((s["last_sync"] for s in syncs if s["source"] == "stock_count"), "Never")

    # Build stock table rows
    rows = ""
    for s in stock:
        tl_total = s["tl_cpt"] + s["tl_jnb"] + s["tl_dbn"]
        grand_total = tl_total + s["home_stock"] + s["pipeline_stock"]
        daily = s["avg_daily"]
        if daily > 0:
            days = round(grand_total / daily)
        else:
            days = 999 if grand_total > 0 else 0

        if days == 0 and s["sold_30d"] > 0:
            badge = '<span class="badge badge-critical">CRITICAL</span>'
        elif days < 7:
            badge = '<span class="badge badge-urgent">URGENT</span>'
        elif days < 14:
            badge = '<span class="badge badge-soon">SOON</span>'
        else:
            badge = '<span class="badge badge-ok">OK</span>'

        cover_str = f"{days}d" if days < 999 else "---"
        title_short = s["title"][:35]

        rows += f"""<tr>
            <td>{badge}</td>
            <td><strong>{s['sku'][:18]}</strong><br><span class="text-muted" style="font-size:11px">{title_short}</span></td>
            <td class="text-center">{s['home_stock']}</td>
            <td class="text-center">{tl_total}</td>
            <td class="text-center hide-mobile">{s['pipeline_stock']}</td>
            <td class="text-center">{cover_str}</td>
            <td class="text-right hide-mobile">{s['sold_30d']}</td>
            <td class="text-right hide-mobile">R{s['revenue_30d']:,.0f}</td>
        </tr>"""

    content = f"""
    <div class="stat-grid">
        <div class="stat"><div class="value text-gold">{total_home}</div><div class="label">Home Stock</div></div>
        <div class="stat"><div class="value" style="color:var(--blue)">{total_tl}</div><div class="label">Takealot</div></div>
        <div class="stat"><div class="value" style="color:var(--orange)">{total_pipeline}</div><div class="label">Pipeline</div></div>
        <div class="stat"><div class="value {'text-red' if zero_stock > 5 else 'text-gold'}">{zero_stock}</div><div class="label">Out of Stock</div></div>
        <div class="stat"><div class="value text-green">R{total_rev:,.0f}</div><div class="label">30d Revenue</div></div>
    </div>

    <div class="card">
        <div class="card-header">
            <h3>Stock Positions</h3>
            <form action="/api/sync/takealot" method="post" style="display:inline">
                <button type="submit" class="btn btn-secondary btn-sm">Sync Takealot</button>
            </form>
        </div>
        <div class="scroll-x">
        <table>
            <thead><tr>
                <th>Status</th><th>Product</th><th>Home</th><th>TL</th>
                <th class="hide-mobile">Pipeline</th><th>Cover</th>
                <th class="hide-mobile">Sold 30d</th><th class="hide-mobile">Rev 30d</th>
            </tr></thead>
            <tbody>{rows}</tbody>
        </table>
        </div>
    </div>

    <div class="card" style="font-size:12px; color:var(--muted)">
        <strong>Last syncs:</strong> Takealot: {last_tl_sync} | Stock count: {last_count}
    </div>
    """
    return page("Dashboard", content, "dashboard")


# ── STOCK COUNT ─────────────────────────────────────────────────────────────
@app.get("/stocktake", response_class=HTMLResponse)
async def stocktake_form():
    products = get_all_products()
    history = get_stock_count_history(20)

    # Product list for quick count
    quick_rows = ""
    for p in products:
        quick_rows += f"""
        <form action="/api/stocktake" method="post" class="quick-count">
            <input type="hidden" name="product_id" value="{p['id']}">
            <div class="name"><strong>{p['sku'][:18]}</strong><br><span class="text-muted" style="font-size:11px">{p['title'][:30]}</span></div>
            <input type="number" name="quantity" min="0" placeholder="Qty" required>
            <button type="submit" class="btn btn-primary btn-sm">Save</button>
        </form>"""

    # History
    hist_rows = ""
    for h in history:
        hist_rows += f"""<tr>
            <td>{h['sku']}</td><td>{h['quantity']}</td>
            <td class="text-muted">{h['counted_at'][:16]}</td>
        </tr>"""

    content = f"""
    <div class="card">
        <h3>Quick Stock Count</h3>
        <p class="text-muted mb-2" style="font-size:12px">Enter current quantity at home for each product. Tap Save to update.</p>
        {quick_rows}
        {('<div class="empty">No products yet. Sync Takealot first.</div>' if not products else '')}
    </div>

    <div class="card">
        <h3>Bulk Count</h3>
        <form action="/api/stocktake/bulk" method="post">
            <div class="form-group">
                <label>Paste counts (one per line: SKU, quantity)</label>
                <textarea name="bulk_data" rows="6" placeholder="CW323, 50&#10;ALCS1, 30&#10;MIXE3, 25"></textarea>
            </div>
            <button type="submit" class="btn btn-primary">Submit Bulk Count</button>
        </form>
    </div>

    <div class="card">
        <h3>Recent Counts</h3>
        <table><thead><tr><th>SKU</th><th>Qty</th><th>When</th></tr></thead>
        <tbody>{hist_rows if hist_rows else '<tr><td colspan="3" class="empty">No counts yet</td></tr>'}</tbody></table>
    </div>
    """
    return page("Stock Count", content, "stocktake")


@app.post("/api/stocktake")
async def submit_stocktake(product_id: int = Form(...), quantity: int = Form(...)):
    record_stock_count(product_id, quantity)
    from database import log_sync
    log_sync("stock_count", "success", f"Product {product_id} counted: {quantity}")
    return RedirectResponse("/stocktake", status_code=303)


@app.post("/api/stocktake/bulk")
async def submit_bulk_stocktake(bulk_data: str = Form(...)):
    conn = get_db()
    lines = [l.strip() for l in bulk_data.strip().split("\n") if l.strip()]
    count = 0
    for line in lines:
        parts = [p.strip() for p in line.split(",")]
        if len(parts) >= 2:
            sku = parts[0]
            try:
                qty = int(parts[1])
            except ValueError:
                continue
            row = conn.execute("SELECT id FROM products WHERE sku = ?", (sku,)).fetchone()
            if row:
                record_stock_count(row["id"], qty)
                count += 1
    conn.close()
    from database import log_sync
    log_sync("stock_count", "success", f"Bulk count: {count} products updated")
    return RedirectResponse("/stocktake", status_code=303)


# ── SUPPLIER ORDERS ─────────────────────────────────────────────────────────
@app.get("/orders", response_class=HTMLResponse)
async def orders_page():
    orders = get_supplier_orders()
    products = get_all_products()

    order_rows = ""
    for o in orders:
        badge = f'<span class="badge badge-{o["status"]}">{o["status"].upper()}</span>'
        items_str = ", ".join(f'{i["product_name"]} ({i["quantity_kg"]}kg)' for i in o["items"][:3])
        if len(o["items"]) > 3:
            items_str += f" +{len(o['items'])-3} more"

        status_buttons = ""
        if o["status"] == "ordered":
            status_buttons = f'<form action="/api/orders/{o["id"]}/status" method="post" style="display:inline"><input type="hidden" name="status" value="in_transit"><button class="btn btn-sm btn-secondary">→ In Transit</button></form>'
        elif o["status"] == "in_transit":
            status_buttons = f'<form action="/api/orders/{o["id"]}/status" method="post" style="display:inline"><input type="hidden" name="status" value="received"><button class="btn btn-sm btn-primary">→ Received</button></form>'
        elif o["status"] == "received":
            status_buttons = f'<form action="/api/orders/{o["id"]}/status" method="post" style="display:inline"><input type="hidden" name="status" value="allocated"><button class="btn btn-sm btn-primary">→ Allocated</button></form>'

        order_rows += f"""<div class="card">
            <div class="card-header">
                <div>{badge} <strong>{o['supplier']}</strong></div>
                <div class="text-gold">R{o['total_cost']:,.0f}</div>
            </div>
            <div style="font-size:12px; color:var(--muted); margin-bottom:6px">
                Ordered: {o['order_date']} | ETA: {o['eta_date'] or 'TBD'}
                {f" | Received: {o['received_date']}" if o['received_date'] else ''}
            </div>
            <div style="font-size:13px; margin-bottom:8px">{items_str}</div>
            {f'<div style="font-size:12px; color:var(--muted); margin-bottom:6px">{o["notes"]}</div>' if o['notes'] else ''}
            <div>{status_buttons}</div>
        </div>"""

    # Product options for order form
    product_options = "".join(f'<option value="{p["id"]}">{p["sku"]} — {p["title"][:30]}</option>' for p in products)

    content = f"""
    {order_rows if order_rows else '<div class="empty">No supplier orders tracked yet</div>'}

    <div class="card">
        <h3>New Supplier Order</h3>
        <form action="/api/orders" method="post">
            <div class="form-group">
                <label>Supplier</label>
                <select name="supplier" required>
                    <option value="">Select supplier</option>
                    <option value="Upstream">Upstream (Primary)</option>
                    <option value="Multisnack">Multisnack</option>
                    <option value="DH">DH (Ding Ho)</option>
                    <option value="Other">Other</option>
                </select>
            </div>
            <div class="flex gap-2">
                <div class="form-group" style="flex:1">
                    <label>Order Date</label>
                    <input type="date" name="order_date" value="{datetime.now().strftime('%Y-%m-%d')}" required>
                </div>
                <div class="form-group" style="flex:1">
                    <label>ETA Date</label>
                    <input type="date" name="eta_date">
                </div>
            </div>
            <div class="form-group">
                <label>Total Cost (R)</label>
                <input type="number" name="total_cost" step="0.01" placeholder="0.00" required>
            </div>
            <div class="form-group">
                <label>Items (one per line: Product Name, KG, Units, R/kg)</label>
                <textarea name="items_text" rows="5" placeholder="Cashew R&S W320, 45.4, 45, 148.80&#10;Almonds NPX, 45.36, 45, 158.20"></textarea>
            </div>
            <div class="form-group">
                <label>Notes</label>
                <input type="text" name="notes" placeholder="COD, delivery details, etc.">
            </div>
            <button type="submit" class="btn btn-primary">Add Order</button>
        </form>
    </div>
    """
    return page("Supplier Orders", content, "orders")


@app.post("/api/orders")
async def create_order(
    supplier: str = Form(...),
    order_date: str = Form(...),
    eta_date: str = Form(""),
    total_cost: float = Form(0),
    items_text: str = Form(""),
    notes: str = Form("")
):
    items = []
    conn = get_db()
    for line in items_text.strip().split("\n"):
        if not line.strip():
            continue
        parts = [p.strip() for p in line.split(",")]
        if len(parts) >= 2:
            name = parts[0]
            kg = float(parts[1]) if len(parts) > 1 else 0
            units = int(parts[2]) if len(parts) > 2 else 0
            cost_kg = float(parts[3]) if len(parts) > 3 else 0

            # Try to match product
            row = conn.execute(
                "SELECT id FROM products WHERE title LIKE ? OR sku LIKE ? LIMIT 1",
                (f"%{name}%", f"%{name}%")
            ).fetchone()
            pid = row["id"] if row else None

            items.append({
                "product_id": pid,
                "product_name": name,
                "quantity_kg": kg,
                "quantity_units": units,
                "cost_per_kg": cost_kg,
                "line_total": round(kg * cost_kg, 2)
            })
    conn.close()

    create_supplier_order(supplier, order_date, eta_date, total_cost, notes, items)
    return RedirectResponse("/orders", status_code=303)


@app.post("/api/orders/{order_id}/status")
async def update_status(order_id: int, status: str = Form(...)):
    update_order_status(order_id, status)
    return RedirectResponse("/orders", status_code=303)


# ── REORDER ENGINE ──────────────────────────────────────────────────────────
@app.get("/reorder", response_class=HTMLResponse)
async def reorder_page():
    suggestions = get_reorder_suggestions()

    total_cost = sum(s["est_cost"] for s in suggestions if s["est_cost"] > 0)

    rows = ""
    for s in suggestions:
        badge_class = {"CRITICAL": "badge-critical", "URGENT": "badge-urgent", "SOON": "badge-soon"}
        badge = f'<span class="badge {badge_class.get(s["urgency"], "")}">{s["urgency"]}</span>'
        rows += f"""<tr>
            <td>{badge}</td>
            <td><strong>{s['sku'][:18]}</strong><br><span class="text-muted" style="font-size:11px">{s['title'][:30]}</span></td>
            <td class="text-center">{s['total_tl']}</td>
            <td class="text-center">{s['home_stock']}</td>
            <td class="text-center">{s['days_cover']}d</td>
            <td class="text-center text-gold"><strong>{s['suggested_qty']}</strong></td>
            <td class="text-right hide-mobile">{s['primary_supplier'] or '?'}</td>
            <td class="text-right">{'R{:,.0f}'.format(s['est_cost']) if s['est_cost'] > 0 else '?'}</td>
        </tr>"""

    # Group by supplier
    by_supplier = {}
    for s in suggestions:
        sup = s["primary_supplier"] or "Unknown"
        by_supplier.setdefault(sup, []).append(s)

    supplier_summary = ""
    for sup, items in by_supplier.items():
        sup_cost = sum(i["est_cost"] for i in items if i["est_cost"] > 0)
        sup_qty = sum(i["suggested_qty"] for i in items)
        supplier_summary += f'<div class="stat"><div class="value text-gold">R{sup_cost:,.0f}</div><div class="label">{sup} ({sup_qty} units)</div></div>'

    content = f"""
    <div class="stat-grid">
        <div class="stat"><div class="value text-red">{len(suggestions)}</div><div class="label">SKUs Need Reorder</div></div>
        <div class="stat"><div class="value text-gold">R{total_cost:,.0f}</div><div class="label">Est. Total Cost</div></div>
        {supplier_summary}
    </div>

    <div class="card">
        <h3>Reorder Suggestions</h3>
        <p class="text-muted mb-2" style="font-size:12px">Based on 30-day sell-through rate, targeting 30 days of cover.</p>
        <div class="scroll-x">
        <table>
            <thead><tr>
                <th>Priority</th><th>Product</th><th>TL</th><th>Home</th>
                <th>Cover</th><th>Order Qty</th>
                <th class="hide-mobile">Supplier</th><th>Est. Cost</th>
            </tr></thead>
            <tbody>{rows if rows else '<tr><td colspan="8" class="empty">All stocked up!</td></tr>'}</tbody>
        </table>
        </div>
    </div>
    """
    return page("Reorder Suggestions", content, "reorder")


# ── PRODUCTS ────────────────────────────────────────────────────────────────
@app.get("/products", response_class=HTMLResponse)
async def products_page():
    products = get_all_products()

    rows = ""
    for p in products:
        margin = ""
        if p["cogs_per_kg"] > 0 and p["takealot_price"] > 0:
            cogs_unit = p["cogs_per_kg"] * p["pack_size_kg"]
            m = round((1 - cogs_unit / p["takealot_price"]) * 100)
            color = "text-green" if m >= 45 else "text-gold" if m >= 30 else "text-red"
            margin = f'<span class="{color}">{m}%</span>'

        rows += f"""<tr>
            <td><strong>{p['sku'][:18]}</strong></td>
            <td>{p['title'][:30]}</td>
            <td class="hide-mobile">{p['primary_supplier'] or '-'}</td>
            <td class="text-right">{'R{:.0f}'.format(p['cogs_per_kg']) if p['cogs_per_kg'] else '-'}</td>
            <td class="text-right">{'R{:.0f}'.format(p['takealot_price']) if p['takealot_price'] else '-'}</td>
            <td class="text-center">{margin or '-'}</td>
        </tr>"""

    content = f"""
    <div class="card">
        <div class="card-header">
            <h3>Product Master List</h3>
            <span class="text-muted" style="font-size:12px">{len(products)} products</span>
        </div>
        <div class="scroll-x">
        <table>
            <thead><tr><th>SKU</th><th>Title</th><th class="hide-mobile">Supplier</th><th>COGS/kg</th><th>TL Price</th><th>Margin</th></tr></thead>
            <tbody>{rows}</tbody>
        </table>
        </div>
    </div>

    <div class="card">
        <h3>Update Product COGS</h3>
        <form action="/api/products/cogs" method="post">
            <div class="form-group">
                <label>Paste COGS data (one per line: SKU, Supplier, R/kg)</label>
                <textarea name="cogs_data" rows="5" placeholder="CW323, Upstream, 148.80&#10;ALCS1, Upstream, 158.20&#10;MIXE3, Multisnack, 162.00"></textarea>
            </div>
            <button type="submit" class="btn btn-primary">Update COGS</button>
        </form>
    </div>
    """
    return page("Products", content, "products")


@app.post("/api/products/cogs")
async def update_cogs(cogs_data: str = Form(...)):
    from database import update_product_supplier_info
    for line in cogs_data.strip().split("\n"):
        parts = [p.strip() for p in line.split(",")]
        if len(parts) >= 3:
            sku = parts[0]
            supplier = parts[1]
            try:
                cogs = float(parts[2])
            except ValueError:
                continue
            update_product_supplier_info(sku, supplier, cogs)
    return RedirectResponse("/products", status_code=303)


# ── API ENDPOINTS ───────────────────────────────────────────────────────────
@app.post("/api/sync/takealot")
async def sync_takealot_endpoint():
    result = sync_takealot()
    return RedirectResponse("/", status_code=303)


@app.get("/api/sync/takealot")
async def sync_takealot_api():
    """API endpoint for scheduled sync (cron/external trigger)."""
    result = sync_takealot()
    return JSONResponse(result)


@app.get("/api/stock")
async def stock_api():
    """JSON API for stock data (for external tools/agents)."""
    return JSONResponse(get_unified_stock())


@app.get("/api/reorder")
async def reorder_api():
    """JSON API for reorder suggestions."""
    return JSONResponse(get_reorder_suggestions())


@app.get("/api/health")
async def health():
    return {"status": "ok", "time": datetime.now().isoformat()}


# ── MAIN ────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    uvicorn.run("app:app", host="0.0.0.0", port=port, reload=True)
