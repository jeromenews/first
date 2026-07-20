"""
Mariah Carey store — whole-store change monitor.

The store is small (~40 products) and rarely changes, so instead of trying
to match one product by name/price, we fingerprint the ENTIRE store-wide
products.json (every product's handle + title + availability) and alert on
ANY change: a new product appearing, one disappearing, or an item flipping
in/out of stock. When alerted, the user just glances at the store.

This reliably catches the signed-insert Daydream vinyl returning, whether it
comes back at its old URL, as a brand-new listing, or in any collection —
because any of those changes the store-wide fingerprint.

Uses the store-wide products.json (paginated), which is the most reliable
endpoint and the one confirmed reachable. Sends a push via ntfy.sh.
"""

import os
import sys
import json
import requests

STORE = "https://mariahcarey.rosecityworks.com"
BUY_LINK = f"{STORE}/collections/daydream-30th-anniversary"
STATE_FILE = "store_state.json"

# Words that, if they appear in a NEW/changed product, make the alert louder.
HOT_WORDS = ["signed", "daydream", "autograph", "insert"]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
    "X-Shopify-Country": "US",
}


def fetch_all_products():
    """Page through the store-wide products.json. Returns list or None on failure."""
    products = []
    for page in range(1, 11):  # up to 2500 products; store has ~40
        url = f"{STORE}/products.json?limit=250&page={page}"
        try:
            r = requests.get(url, headers=HEADERS, timeout=25)
        except requests.RequestException as exc:
            print(f"  request error page {page}: {exc}")
            return None
        if r.status_code != 200:
            print(f"  page {page} status {r.status_code}")
            return None
        try:
            batch = r.json().get("products", [])
        except json.JSONDecodeError:
            print(f"  page {page} not JSON (possible block/CAPTCHA)")
            return None
        if not batch:
            break
        products.extend(batch)
    return products


def fingerprint(products):
    """One sorted line per product: handle | availability | title."""
    lines = []
    for p in products:
        handle = p.get("handle", "")
        title = (p.get("title") or "").replace("|", "/")
        any_avail = any(v.get("available") for v in p.get("variants", []))
        lines.append(f"{handle}|{'A' if any_avail else '-'}|{title}")
    lines.sort()
    return lines


def load_prev():
    try:
        with open(STATE_FILE) as f:
            return json.load(f).get("fingerprint")
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def save(fp):
    try:
        with open(STATE_FILE, "w") as f:
            json.dump({"fingerprint": fp}, f)
    except OSError as exc:
        print(f"  could not save state: {exc}")


def diff(prev, curr):
    """Return (added, removed, changed) lists of human-readable strings."""
    prev_map = {ln.split("|", 1)[0]: ln for ln in prev}
    curr_map = {ln.split("|", 1)[0]: ln for ln in curr}
    added, removed, changed = [], [], []
    for h, ln in curr_map.items():
        if h not in prev_map:
            added.append(ln)
        elif prev_map[h] != ln:
            # same handle, availability or title changed
            changed.append(f"{prev_map[h]}  ->  {ln}")
    for h, ln in prev_map.items():
        if h not in curr_map:
            removed.append(ln)
    return added, removed, changed


def notify(topic, title, message, priority="default"):
    try:
        requests.post(
            f"https://ntfy.sh/{topic}",
            data=message.encode("utf-8"),
            headers={
                "Title": title,
                "Priority": priority,
                "Tags": "rotating_light,cd",
                "Click": BUY_LINK,
                "Actions": f"view, Open store, {BUY_LINK}",
            },
            timeout=15,
        )
        print(f"Notification sent: {title}")
    except requests.RequestException as exc:
        print(f"Failed to send notification: {exc}")


def main():
    topic = os.environ.get("NTFY_TOPIC")
    if not topic:
        print("NTFY_TOPIC not set — cannot send alerts.")
        return 1

    products = fetch_all_products()
    if products is None:
        # Couldn't read the store — could be a temporary block. Warn, don't crash.
        notify(topic,
               "WARNING: store monitor couldn't read the store",
               "The monitor failed to read the Mariah store this run (possible "
               "block or outage). It'll retry next cycle; check manually if this "
               "keeps happening.",
               priority="high")
        print("Fetch failed — sent warning.")
        return 0

    curr = fingerprint(products)
    print(f"Fetched {len(curr)} products.")

    prev = load_prev()
    if prev is None:
        # First run: just record the baseline, no alert.
        save(curr)
        print("Baseline recorded. No alert on first run.")
        return 0

    added, removed, changed = diff(prev, curr)

    if not (added or removed or changed):
        print("No change since last run.")
        return 0

    # Something changed — build the alert.
    lines = []
    if added:
        lines.append("NEW products:")
        lines += [f"  + {a}" for a in added]
    if changed:
        lines.append("CHANGED (stock/title):")
        lines += [f"  ~ {c}" for c in changed]
    if removed:
        lines.append("REMOVED products:")
        lines += [f"  - {r}" for r in removed]

    body = "\n".join(lines)

    # Louder alert if anything hot (signed / daydream) is involved.
    blob = body.lower()
    hot = any(w in blob for w in HOT_WORDS)
    title = ("POSSIBLE DAYDREAM SIGNED RESTOCK!" if hot
             else "Mariah store changed — go look")
    priority = "urgent" if hot else "high"

    notify(topic, title,
           "The store's product list changed:\n\n" + body +
           "\n\nTap to open the store.",
           priority=priority)
    print("ALERT SENT.\n" + body)

    save(curr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
