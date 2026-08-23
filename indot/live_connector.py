"""
Live INDOT connector.

IMPORTANT, VERIFIED FACT (this project, repeated checks): direct requests to
www.in.gov from every environment tested so far (this sandbox's curl, and the
harness's own WebFetch tool) fail before a response is ever received -- a TCP-level
timeout, not an HTTP error. Two alternate INDOT subdomains (erms12c.indot.in.gov,
entapps.indot.in.gov) return a fast, clean HTTP 403 (genuine access control, not
bypassed).

This module is therefore INFRASTRUCTURE. Its pure logic (URL resolution, date
parsing, dedup, schema shaping) is unit-tested without network access in
tests/test_live_connector.py. The network-dependent functions have NOT been run
against live INDOT data from this environment -- that validation is intentionally
deferred to a GitHub Actions run with real outbound access. Do not treat this
module as live-validated until that run has actually happened.

TWO-STAGE ARCHITECTURE (this design was forced by a real failure: the first GitHub
Actions run of the single-stage version was cancelled after 10m42s still running,
because it downloaded a full Planholder PDF for every discovered letting link
before checking whether that letting was even upcoming -- see the final report
for the exact cause):

  Stage 1 -- LETTING DISCOVERY (discover_upcoming_lettings):
    Fetches the archive index once, visits each candidate letting page ONCE with a
    bounded 15s timeout, reads only its date and (if upcoming) whether a Planholder
    PDF link exists. Never downloads or parses a PDF. Bounded to the first
    MAX_UPCOMING_LETTINGS (5) upcoming lettings, sorted ascending by date.

  Stage 2 -- PLANHOLDER INGESTION (get_planholder_list / _candidate_count_for_letting):
    For ONE specific letting the caller has already chosen from Stage 1's output,
    downloads and parses its Planholder PDF (reusing parse_indot.py). This is the
    expensive step and is never invoked automatically for every discovered letting.
"""
import urllib.request
import urllib.parse
import re
import json
import hashlib
import os
from datetime import datetime, timezone

from parse_indot import load_blocks, PLAN_NAME_RE, full_text

UA = {'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15'}
CACHE_DIR = "live_cache"
CACHE_INDEX = os.path.join(CACHE_DIR, "cache_index.json")
BASE_URL = "https://www.in.gov"
LETTING_ARCHIVE_URL = "https://www.in.gov/indot/doing-business-with-indot/home/contracts/letting-archives2/"

BPH_PATTERNS = ["b-and-ph", "b_and_ph", "bidders_list", "bidderlist", "bidderslist",
                "b_ph", "b-ph", "bidders-list", "bidders list", "and-ph", "and_ph"]

MAX_UPCOMING_LETTINGS = 5
DISCOVERY_TIMEOUT_SECONDS = 15

# Matches a date like "September 2, 2026" or "Wednesday, September 2, 2026" appearing
# anywhere in a letting page's text (title, heading, or body) -- reused for BOTH the
# "which letting is this" question and the "has it already happened" filter.
LETTING_DATE_RE = re.compile(
    r'(?:[A-Za-z]+,\s*)?([A-Za-z]+ \d{1,2},\s*\d{4})'
)


# ------------------------------------------------------------------
# pure helpers (no network) -- unit tested directly
# ------------------------------------------------------------------

def resolve_url(base, link):
    """Resolve a possibly-relative link against an absolute base URL.
    Always returns an absolute https://www.in.gov/... URL for in.gov-relative links."""
    return urllib.parse.urljoin(base, link)


def normalize_url(url):
    """Normalize a URL for deduplication: strip a single trailing slash, lowercase
    scheme+host only (path casing on this site is meaningful, e.g. mixed-case PDF
    filenames), drop any fragment."""
    parsed = urllib.parse.urlsplit(url)
    scheme = parsed.scheme.lower()
    netloc = parsed.netloc.lower()
    path = parsed.path.rstrip("/")
    return urllib.parse.urlunsplit((scheme, netloc, path, parsed.query, ""))


def dedupe_urls(urls):
    """Deduplicate a list of URLs by their normalized form, preserving first-seen order."""
    seen = set()
    out = []
    for u in urls:
        n = normalize_url(u)
        if n not in seen:
            seen.add(n)
            out.append(u)
    return out


def extract_letting_date(page_text):
    """Extract the letting date from a letting page's own text. Returns a
    datetime.date or None if no date could be found. Never falls back to
    guessing a date from the URL."""
    m = LETTING_DATE_RE.search(page_text)
    if not m:
        return None
    raw = m.group(1).replace(",", "")
    try:
        return datetime.strptime(raw, "%B %d %Y").date()
    except ValueError:
        return None


def is_upcoming(letting_date, today=None):
    """True only if letting_date is known and is today or in the future."""
    if letting_date is None:
        return False
    today = today or datetime.now(timezone.utc).date()
    return letting_date >= today


def is_archive_index_url(url):
    """True if `url` (after normalization) IS the archive index page itself,
    not a specific letting page -- guards against the index accidentally being
    treated as one of its own discovered links."""
    return normalize_url(url) == normalize_url(LETTING_ARCHIVE_URL)


def find_planholder_link(html):
    """Find the first PDF link in `html` matching a known Planholder/Bidders-List
    naming pattern. Returns the raw (possibly relative) href, or None."""
    pdf_links = re.findall(r'href="([^"]+\.pdf)"', html, re.I)
    for l in pdf_links:
        base = l.split("/")[-1].lower()
        if any(p in base for p in BPH_PATTERNS):
            return l
    return None


# ------------------------------------------------------------------
# network-dependent (not exercised against live data from this environment)
# ------------------------------------------------------------------

def _load_cache_index():
    os.makedirs(CACHE_DIR, exist_ok=True)
    if os.path.exists(CACHE_INDEX):
        return json.load(open(CACHE_INDEX))
    return {}


def _save_cache_index(idx):
    json.dump(idx, open(CACHE_INDEX, "w"), indent=2)


def fetch_with_cache(url, force_refresh=False, timeout=30):
    """Fetch a URL, caching by content checksum. Returns (bytes, cache_entry).
    Never silently reuses a stale document -- always re-fetches and compares checksum;
    only the LOCAL FILE WRITE is skipped if the checksum is unchanged.
    `url` may be relative; it is resolved against BASE_URL first (minimal fix,
    fetch_with_cache's caching behavior itself is unchanged). `timeout` defaults to
    30s for Stage 2 (PDF ingestion, unchanged); Stage 1 discovery passes a shorter,
    bounded timeout explicitly -- see DISCOVERY_TIMEOUT_SECONDS."""
    url = resolve_url(BASE_URL, url)
    idx = _load_cache_index()
    req = urllib.request.Request(url, headers=UA)
    data = urllib.request.urlopen(req, timeout=timeout).read()
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


def get_planholder_list(letting_page_url):
    """Given a specific letting page, find and fetch its CURRENT Proposal Planholder
    List. Returns (pdf_bytes, cache_entry) or (None, {"reason": ...}) if not yet posted."""
    html, page_entry = fetch_with_cache(letting_page_url)
    html_text = html.decode("utf-8", errors="ignore")
    bph_link = find_planholder_link(html_text)
    if not bph_link:
        return None, {"reason": "no_prebid_candidate_list"}
    bph_url = resolve_url(letting_page_url, bph_link)
    pdf_data, pdf_entry = fetch_with_cache(bph_url)
    return pdf_data, pdf_entry


def get_bid_deadline(letting_page_html):
    """Extract the bid-received-until timestamp for leakage-boundary enforcement."""
    m = re.search(r'(\d{1,2}:\d{2}\s*[AP]M)', letting_page_html)
    return m.group(1) if m else None


def _candidate_count_for_letting(pdf_path):
    """Reuses parse_indot.py's validated block/candidate logic directly -- does not
    duplicate its regexes. Returns the total Valid-For-Bid candidate count across all
    contracts in this Planholder PDF."""
    txt = full_text(pdf_path)
    blocks = load_blocks(txt)
    total = 0
    for cid, segs in blocks.items():
        ptxt = segs[0]
        total += sum(1 for m in PLAN_NAME_RE.finditer(ptxt) if m.group(2) == "Yes")
    return total


def _discovery_record(letting_date, letting_url, planholder_list_available,
                       planholder_url=None, reason=None):
    """Builds a Stage-1 result record with exactly the required schema. No
    candidate_count, retrieval_timestamp, or checksum here -- those only exist
    once a PDF is actually downloaded, which Stage 1 never does."""
    rec = {
        "letting_date": letting_date.isoformat() if letting_date else None,
        "letting_page_url": letting_url,
        "planholder_list_available": planholder_list_available,
        "planholder_url": planholder_url,
    }
    if reason is not None:
        rec["reason"] = reason
    return rec


def discover_upcoming_lettings(today=None, max_results=MAX_UPCOMING_LETTINGS):
    """STAGE 1 ONLY: discover up to `max_results` upcoming lettings. Bounded and fast
    by design -- never downloads or parses a Planholder PDF (that is Stage 2, see
    get_planholder_list / _candidate_count_for_letting, called explicitly by the
    caller for ONE chosen letting afterward). NOT executed against live data from
    this environment -- see the module docstring.

    Returns (results, index_entry) where each result matches exactly:
      letting_date, letting_page_url, planholder_list_available, planholder_url,
      reason (only if unavailable/error)
    sorted ascending by letting_date, capped at `max_results` records.
    """
    html, index_entry = fetch_with_cache(LETTING_ARCHIVE_URL, timeout=DISCOVERY_TIMEOUT_SECONDS)
    html_text = html.decode("utf-8", errors="ignore")

    raw_links = re.findall(r'href="([^"]*letting[^"]*)"', html_text, re.I)
    resolved_links = [resolve_url(LETTING_ARCHIVE_URL, l) for l in raw_links]
    resolved_links = [l for l in resolved_links if not is_archive_index_url(l)]
    letting_urls = dedupe_urls(resolved_links)

    upcoming = []
    for letting_url in letting_urls:
        try:
            page_html, _page_entry = fetch_with_cache(letting_url, timeout=DISCOVERY_TIMEOUT_SECONDS)
        except Exception as e:
            # one failed page is recorded but does NOT stop discovery of the rest
            upcoming.append((None, _discovery_record(
                None, letting_url, False, reason=f"fetch_error: {e}")))
            continue

        page_text = page_html.decode("utf-8", errors="ignore")
        letting_date = extract_letting_date(page_text)
        if not is_upcoming(letting_date, today=today):
            # past letting, or date unrecoverable -- skip silently, not an error
            continue

        # Planholder DETECTION only: inspect the already-fetched HTML for a link,
        # resolve it -- never fetch the PDF itself in Stage 1.
        bph_link = find_planholder_link(page_text)
        if bph_link:
            bph_url = resolve_url(letting_url, bph_link)
            upcoming.append((letting_date, _discovery_record(letting_date, letting_url, True, planholder_url=bph_url)))
        else:
            upcoming.append((letting_date, _discovery_record(
                letting_date, letting_url, False, reason="no_prebid_candidate_list")))

    # sort ascending by date; records with no recoverable date (fetch errors) sort last
    upcoming.sort(key=lambda pair: (pair[0] is None, pair[0]))
    results = [rec for _date, rec in upcoming[:max_results]]
    return results, index_entry


def run_discovery_report():
    """Explicit, callable entry point for a live test run (e.g. from a GitHub Actions
    step). Never called on import -- only invoked when explicitly requested.
    Stage 1 ONLY -- does not download or parse any Planholder PDF."""
    results, index_entry = discover_upcoming_lettings()
    print(f"discovered {len(results)} upcoming letting(s) (capped at {MAX_UPCOMING_LETTINGS})")
    for r in results:
        print(f"  {r['letting_date']}  {r['letting_page_url']}  "
              f"planholder_available={r['planholder_list_available']}  "
              f"planholder_url={r.get('planholder_url')}  reason={r.get('reason')}")
    return results


if __name__ == "__main__":
    run_discovery_report()
