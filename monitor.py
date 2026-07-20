"""
Daydream (30th Anniversary) Vinyl 3LP w/ Exclusive Signed Insert
— restock checker, USA zone.

Design goals for THIS run:
  * Catch the item even if slightly renamed  -> also alert on ANY change to
    the collection's product lineup (new item / different product count).
  * Never fail silently -> if the store looks like it's blocking us
    (CAPTCHA / block page / error / empty response), send a DISTINCT
    "monitor may be blocked" alert so you know to check by hand.
  * Present as a normal US shopper; rotate browser identity a little.

Sources checked:
  1. Shopify products.json for the collection (structured availability).
  2. The collection HTML page (name + price + lineup fingerprint).
  3. Store search for "signed insert".

Runs free on GitHub Actions — see .github/workflows/check-restock.yml
State (the last-seen product lineup) is saved to lineup_state.json in the
repo so we can detect changes across runs.
"""

import os
import sys
import json
import random
import hashlib
import requests

# --- What we're looking for -------------------------------------------------
PRODUCT_NAME_HINTS = ["signed insert", "exclusive signed", "autograph", "signed"]
EXPECTED_PRICE = "99.98"

BUY_LINK = "https://mariahcarey.rosecityworks.com/collections/daydream-30th-anniversary"

US_PARAMS = {"locale": "en", "region_country": "US", "currency": "USD"}

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.4 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
]

def _headers():
    return {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept-Language": "en-US,en;q=0.9",
        "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
        "X-Shopify-Country": "US",
    }

COLLECTION_HTML = "https://mariahcarey.rosecityworks.com/collections/daydream-30th-anniversary"
COLLECTION_JSON = "https://mariahcarey.rosecityworks.com/collections/daydream-30th-anniversary/products.json"
SEARCH_URL = "https://mariahcarey.rosecityworks.com/search"

STATE_FILE = "lineup_state.json"

# --- Signals that we're being blocked / not seeing the real page ------------
BLOCK_SIGNALS = [
    "captcha", "are you a robot", "access denied", "cloudflare",
    "attention required", "unusual traffic", "verify you are human",
    "request blocked", "bot detection",
]

def looks_blocked(resp):
    """Return a reason string if the response looks like a block/error, else None."""
    if resp is None:
        return "no response (network error / timeout)"
    if resp.status_code in (401, 403, 429):
        return f"HTTP {resp.status_code} (blocked / rate-limited)"
    if resp.status_code >= 500:
        return f"HTTP {resp.status_code} (server error)"
    body = resp.text.lower()
    if len(body) < 400:
        return f"suspiciously small response ({len(body)} bytes)"
    for sig in BLOCK_SIGNALS:
        if sig in body:
            return f"block keyword detected: '{sig}'"
    return None


def _get(url, params=None):
    try:
        return requests.get(url, headers=_headers(),
                            params={**US_PARAMS, **(params or {})}, timeout=25)
    except requests.RequestException as exc:
        print(f"  request error for {url}: {exc}")
        return None


# --- Checks -----------------------------------------------------------------

def check_products_json():
    """Strongest signal: structured data with real availability + lineup fingerprint."""
    r = _get(COLLECTION_JSON, params={"limit": 250})
    block = looks_blocked(r)
    if block:
        return {"blocked": block, "available_url": None, "lineup": None}

    try:
        data = r.json()
    except json.JSONDecodeError:
        print("[products.json] not JSON")
        return {"blocked": "products.json returned non-JSON", "available_url": None, "lineup": None}

    lineup = []
    available_url = None
    for product in data.get("products", []):
        title = product.get("title") or ""
        handle = product.get("handle", "")
        variants = product.get("variants", [])
        any_available = any(v.get("available") for v in variants)
        lineup.append(f"{handle}|{'A' if any_available else '-'}")

        tl = title.lower()
        if any(h in tl for h in PRODUCT_NAME_HINTS) and any_available:
            available_url = f"https://mariahcarey.rosecityworks.com/products/{handle}"
            print(f"[products.json] AVAILABLE match: {title}")

    lineup.sort()
    print(f"[products.json] {len(lineup)} products, lineup fingerprint captured")
    return {"blocked": None, "available_url": available_url, "lineup": lineup}


def check_collection_html():
    r = _get(COLLECTION_HTML)
    block = looks_blocked(r)
    if block:
        return {"blocked": block, "name_hit": False, "price_hit": False}
    text = r.text.lower()
    name_hit = any(h in text for h in PRODUCT_NAME_HINTS)
    price_hit = EXPECTED_PRICE in text
    print(f"[collection html] name_hit={name_hit} price_hit={price_hit}")
    return {"blocked": None, "name_hit": name_hit, "price_hit": price_hit}


def check_search():
    r = _get(SEARCH_URL, params={"q": "signed insert"})
    block = looks_blocked(r)
    if block:
        return {"blocked": block, "hit": False}
    text = r.text.lower()
    hit = any(h in text for h in PRODUCT_NAME_HINTS)
    print(f"[search] hit={hit}")
    return {"blocked": None, "hit": hit}


# --- Lineup change detection ------------------------------------------------

def load_prev_lineup():
    try:
        with open(STATE_FILE) as f:
            return json.load(f).get("lineup")
    except (FileNotFoundError, json.JSONDecodeError):
        return None

def save_lineup(lineup):
    try:
        with open(STATE_FILE, "w") as f:
            json.dump({"lineup": lineup}, f)
    except OSError as exc:
        print(f"  could not save state: {exc}")


# --- Notifications ----------------------------------------------------------

def notify(topic, title, message, priority="urgent", click=BUY_LINK):
    try:
        requests.post(
            f"https://ntfy.sh/{topic}",
            data=message.encode("utf-8"),
            headers={
                "Title": title,
                "Priority": priority,
                "Tags": "rotating_light,cd",
                "Click": click,
                "Actions": f"view, Open store, {click}",
            },
            timeout=15,
        )
        print(f"Notification sent: {title}")
    except requests.RequestException as exc:
        print(f"Failed to send notification: {exc}")


# --- Main -------------------------------------------------------------------

def main():
    topic = os.environ.get("NTFY_TOPIC")
    if not topic:
        print("NTFY_TOPIC not set — cannot send alerts.")
        return 1

    js = check_products_json()
    html = check_collection_html()
    srch = check_search()

    # 1) Blocked? Every source failing the block test = we likely can't see the page.
    block_reasons = [x["blocked"] for x in (js, html, srch) if x.get("blocked")]
    if len(block_reasons) == 3:
        notify(topic,
               "WARNING: Vinyl monitor may be blocked",
               "The monitor could not read the store on this run:\n"
               + "\n".join(f"- {b}" for b in block_reasons)
               + "\n\nCheck the page manually to be safe.",
               priority="high")
        print("All sources blocked — sent warning, skipping lineup save.")
        return 0
    elif block_reasons:
        # Partial trouble: note it but keep going with what worked.
        print(f"Partial block/errors this run: {block_reasons}")

    restock_reasons = []
    buy_url = BUY_LINK

    # 2) Direct availability (best signal)
    if js.get("available_url"):
        restock_reasons.append("Store data shows it AVAILABLE to order")
        buy_url = js["available_url"]

    # 3) Name/price on the collection page
    if html.get("name_hit"):
        restock_reasons.append("Signed-insert name is on the collection page")
    if html.get("price_hit") and html.get("name_hit"):
        restock_reasons.append("$99.98 price present alongside the name")

    # 4) Search surfaced it
    if srch.get("hit"):
        restock_reasons.append("Appears in store search")

    # 5) Lineup changed (catches renames / brand-new listings)
    lineup_changed = False
    if js.get("lineup") is not None:
        prev = load_prev_lineup()
        if prev is not None and prev != js["lineup"]:
            lineup_changed = True
            restock_reasons.append("The collection's product lineup CHANGED "
                                   "(new/renamed item?) — worth a look")
        save_lineup(js["lineup"])

    if restock_reasons:
        notify(topic,
               "RESTOCK? Daydream Signed Vinyl",
               "Possible restock of the signed-insert vinyl ($99.98).\n\n"
               "Why:\n" + "\n".join(f"- {r}" for r in restock_reasons)
               + "\n\nTap to open the store and buy fast.",
               click=buy_url)
        print("ALERT SENT.", restock_reasons)
    else:
        print("Nothing yet. Will check again next run.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
