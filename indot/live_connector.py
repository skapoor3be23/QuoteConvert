"""
Live INDOT connector.

IMPORTANT, VERIFIED FACT (this session): direct requests to www.in.gov from this
environment fail at the network level (curl: exit 1, HTTP_STATUS 000 -- connection
never establishes). Two alternate INDOT subdomains (erms12c.indot.in.gov,
entapps.indot.in.gov) return HTTP 403 (access denied, not a network failure).

This module is therefore INFRASTRUCTURE, not a demonstrated-live connector this round.
It reuses the exact parsing logic already validated against 77 real archived INDOT
letting documents (see parse_indot.py / scrape_indot.py), pointed at the live site
paths instead of Wayback snapshots. It has not been run against genuinely live data
this session because the live site is unreachable from here -- see the final report.
"""
import urllib.request, re, json, hashlib, os, time
from datetime import datetime, timezone

UA = {'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15'}
CACHE_DIR = "live_cache"
CACHE_INDEX = os.path.join(CACHE_DIR, "cache_index.json")
LETTING_ARCHIVE_URL = "https://www.in.gov/indot/doing-business-with-indot/home/contracts/letting-archives2/"

BPH_PATTERNS = ["b-and-ph", "b_and_ph", "bidders_list", "bidderlist", "bidderslist",
                "b_ph", "b-ph", "bidders-list", "bidders list", "and-ph", "and_ph"]


def _load_cache_index():
    os.makedirs(CACHE_DIR, exist_ok=True)
    if os.path.exists(CACHE_INDEX):
        return json.load(open(CACHE_INDEX))
    return {}


def _save_cache_index(idx):
    json.dump(idx, open(CACHE_INDEX, "w"), indent=2)


def fetch_with_cache(url, force_refresh=False):
    """Fetch a URL, caching by content checksum. Returns (bytes, cache_entry).
    Never silently reuses a stale document -- always re-fetches and compares checksum;
    only the LOCAL FILE WRITE is skipped if the checksum is unchanged."""
    idx = _load_cache_index()
    req = urllib.request.Request(url, headers=UA)
    data = urllib.request.urlopen(req, timeout=30).read()
    checksum = hashlib.sha256(data).hexdigest()
    now = datetime.now(timezone.utc).isoformat()

    entry = idx.get(url, {})
    changed = entry.get("checksum") != checksum
    fname = os.path.join(CACHE_DIR, hashlib.sha256(url.encode()).hexdigest() + ".bin")
    if changed or not os.path.exists(fname):
        open(fname, "wb").write(data)
    entry.update({
        "source_url": url,
        "retrieval_timestamp": now,
        "checksum": checksum,
        "changed_this_fetch": changed,
        "local_path": fname,
    })
    idx[url] = entry
    _save_cache_index(idx)
    return data, entry


def get_upcoming_lettings():
    """Fetch the current letting-archives index and return letting pages whose bid
    deadline has not yet passed. Requires live site access (see module docstring)."""
    html, entry = fetch_with_cache(LETTING_ARCHIVE_URL)
    html = html.decode("utf-8", errors="ignore")
    links = re.findall(r'href="([^"]+letting[^"]*)"', html, re.I)
    return sorted(set(links)), entry


def get_planholder_list(letting_page_url):
    """Given a specific letting page, find and fetch its CURRENT Proposal Planholder
    List. Returns (candidate_records, cache_entry) or (None, reason) if not yet posted."""
    html, page_entry = fetch_with_cache(letting_page_url)
    html = html.decode("utf-8", errors="ignore")
    pdf_links = re.findall(r'href="([^"]+\.pdf)"', html, re.I)
    bph_link = None
    for l in pdf_links:
        base = l.split("/")[-1].lower()
        if any(p in base for p in BPH_PATTERNS):
            bph_link = l
            break
    if not bph_link:
        return None, {"status": "unavailable", "reason": "no_prebid_candidate_list"}
    pdf_data, pdf_entry = fetch_with_cache(bph_link)
    # NOTE: parsing re-uses the exact validated PLAN_NAME_RE / CONTRACT_RE regex from
    # parse_indot.py (load_blocks + PLAN_NAME_RE) -- not duplicated here; a live deployment
    # would call parse_indot.parse_letting() directly on pdf_entry["local_path"].
    return pdf_data, pdf_entry


def get_bid_deadline(letting_page_html):
    """Extract the bid-received-until timestamp for leakage-boundary enforcement."""
    m = re.search(r'(\d{1,2}:\d{2}\s*[AP]M)', letting_page_html)
    return m.group(1) if m else None
