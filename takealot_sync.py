"""
NP Stock Manager — Takealot API sync
Reuses proven logic from takealot_stock_check.py
"""

import os
import requests
import time
from datetime import datetime, timedelta
from database import (
    get_db, seed_products_from_takealot, update_takealot_stock, log_sync
)

API_KEY = os.environ.get(
    "TAKEALOT_API_KEY",
    "90c75586a6be263747e11de4342d38a79846030632bfc887bc7710291b9efb22b065cd1eaa2f947fd0ba8597d37c2a1ab7acb3d69f24a7e272ec81845999bede"
)
BASE_URL = "https://marketplace-api.takealot.com/v1"
HEADERS = {"X-API-Key": API_KEY}


def fetch_all(endpoint, extra_params=None, retries=3):
    """Fetch all pages using continuation_token pagination."""
    all_items = []
    params = dict(extra_params or {})
    while True:
        for attempt in range(retries):
            try:
                resp = requests.get(
                    f"{BASE_URL}{endpoint}", headers=HEADERS,
                    params=params, timeout=60
                )
                if resp.ok:
                    break
                if resp.status_code >= 500 and attempt < retries - 1:
                    time.sleep(2 ** attempt)
                    continue
                resp.raise_for_status()
            except requests.exceptions.Timeout:
                if attempt < retries - 1:
                    time.sleep(2 ** attempt)
                    continue
                raise
        data = resp.json()
        items = data.get("items", [])
        all_items.extend(items)
        token = data.get("continuation_token")
        if not token:
            break
        params = {"continuation_token": token}
    return all_items


def get_offers():
    """Fetch all offers with live warehouse stock data."""
    return fetch_all("/offers", {
        "limit": 1000,
        "expands": ["takealot_warehouse_stock", "seller_warehouse_stock"],
    })


def sync_takealot():
    """Pull latest Takealot data into unified DB."""
    try:
        offers = get_offers()
        buyable = [o for o in offers if o.get("status") == "buyable"]

        # Seed/update product list
        seed_products_from_takealot(buyable)

        # Update stock positions and sales velocity
        update_takealot_stock(buyable)

        summary = {
            "total_offers": len(offers),
            "buyable": len(buyable),
            "zero_stock": sum(
                1 for o in buyable
                if sum(r.get("quantity_available", 0) for r in o.get("takealot_warehouse_stock", [])) == 0
            ),
        }

        log_sync("takealot", "success",
                 f"{summary['buyable']} buyable, {summary['zero_stock']} at zero stock")
        return {"status": "ok", **summary}

    except Exception as e:
        log_sync("takealot", "error", str(e))
        return {"status": "error", "message": str(e)}
