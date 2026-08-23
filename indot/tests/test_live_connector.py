import sys
from pathlib import Path
TEST_DIR = Path(__file__).resolve().parent
PROJECT_DIR = TEST_DIR.parent
sys.path.insert(0, str(PROJECT_DIR))

from datetime import date
from unittest.mock import patch
import live_connector as lc

print("=" * 78)
print("TEST: relative URL resolution")
print("=" * 78)
r1 = lc.resolve_url("https://www.in.gov/indot/doing-business-with-indot/home/contracts/letting-archives2/",
                     "/indot/doing-business-with-indot/files/Bidders-List_7.1.pdf")
assert r1 == "https://www.in.gov/indot/doing-business-with-indot/files/Bidders-List_7.1.pdf", r1
print("PASS:", r1)

r2 = lc.resolve_url("https://www.in.gov/indot/doing-business-with-indot/home/contracts/letting-archives2/wednesday,-september-2,-2026-regular-letting/",
                     "Bidders-List_7.1.pdf")
assert r2 == "https://www.in.gov/indot/doing-business-with-indot/home/contracts/letting-archives2/wednesday,-september-2,-2026-regular-letting/Bidders-List_7.1.pdf", r2
print("PASS (page-relative):", r2)

print("\n" + "=" * 78)
print("TEST: spaced contract ID normalization (reused from parse_indot, not duplicated)")
print("=" * 78)
from parse_indot import CONTRACT_RE
m = CONTRACT_RE.search("R -43365-A")
assert m is not None
assert m.group(0).replace(" ", "") == "R-43365-A"
print("PASS: 'R -43365-A' -> 'R-43365-A'")

print("\n" + "=" * 78)
print("TEST: extract_letting_date_from_link -- supported and unsupported formats")
print("=" * 78)
cases = [
    ("Wednesday, September 2, 2026 - Regular Letting", date(2026, 9, 2)),
    ("Wednesday, August 5, 2026 - Regular Letting", date(2026, 8, 5)),
    ("Wednesday, Nov. 15, 2023 - Regular Letting", date(2023, 11, 15)),
    ("July 12, 2023 - Regular Letting", date(2023, 7, 12)),
]
for text, expected in cases:
    got = lc.extract_letting_date_from_link(text)
    assert got == expected, f"{text!r} -> {got}, expected {expected}"
    print(f"PASS: {text!r} -> {got}")

unsupported = "Letting Archives"
got_none = lc.extract_letting_date_from_link(unsupported)
assert got_none is None
print(f"PASS: unsupported text {unsupported!r} -> None (skipped, not guessed)")

print("\n" + "=" * 78)
print("TEST: HTMLParser-based anchor extraction (href + visible text)")
print("=" * 78)
html = '''<a href="/a/wednesday,-past/">Wednesday, July 1, 2020 - Regular Letting</a>
<a href="/a/wednesday,-future/">Wednesday, September 10, 2026 - Regular Letting</a>'''
anchors = lc.extract_anchor_links(html)
assert anchors == [
    ("/a/wednesday,-past/", "Wednesday, July 1, 2020 - Regular Letting"),
    ("/a/wednesday,-future/", "Wednesday, September 10, 2026 - Regular Letting"),
], anchors
print("PASS:", anchors)

print("\n" + "=" * 78)
print("TEST: no-planholder state")
print("=" * 78)
assert lc.find_planholder_link('<a href="/indot/files/NoticeToBidders.pdf">Notice</a>') is None
print("PASS: no Planholder-pattern PDF link -> None")
assert lc.find_planholder_link('<a href="/indot/files/B-and-PH-List_9.2.pdf">Bidders List</a>') == "/indot/files/B-and-PH-List_9.2.pdf"
print("PASS: real Planholder pattern found")

print("\n" + "=" * 78)
print("TEST: archive index safety")
print("=" * 78)
assert lc.is_archive_index_url(lc.LETTING_ARCHIVE_URL) is True
assert lc.is_archive_index_url(lc.LETTING_ARCHIVE_URL.rstrip("/")) is True
assert lc.is_archive_index_url("https://www.in.gov/indot/.../wednesday,-april-8,-2026-regular-letting/") is False
print("PASS")

print("\n" + "=" * 78)
print("TEST: duplicate URL deduplication (via normalize_url, exercised directly)")
print("=" * 78)
urls = [
    "https://www.in.gov/indot/.../wednesday,-x/",
    "https://www.in.gov/indot/.../wednesday,-x",
    "https://WWW.IN.GOV/indot/.../wednesday,-x/",
]
assert len({lc.normalize_url(u) for u in urls}) == 1
print("PASS: 3 equivalent URLs normalize to 1")

# ============================================================
# THE CRITICAL REGRESSION TEST: bounded discover_upcoming_lettings(), with
# explicit request counting to prove the architecture actually stops early.
# ============================================================

# 10 links: 6 future, 1 today, 3 past. Only future/today survive local filtering;
# with MAX_UPCOMING_LETTINGS=5, one of the 7 valid candidates must still be excluded.
ARCHIVE_HTML = '''
<html><body>
<a href="/indot/.../letting-archives2/">Letting Archives</a>
<a href="/indot/.../letting-archives2/past-1/">Wednesday, July 1, 2020 - Regular Letting</a>
<a href="/indot/.../letting-archives2/past-2/">Wednesday, August 6, 2025 - Regular Letting</a>
<a href="/indot/.../letting-archives2/past-3/">Wednesday, Nov. 15, 2023 - Regular Letting</a>
<a href="/indot/.../letting-archives2/today/">Wednesday, August 24, 2026 - Regular Letting</a>
<a href="/indot/.../letting-archives2/future-1/">Wednesday, September 2, 2026 - Regular Letting</a>
<a href="/indot/.../letting-archives2/future-2/">Wednesday, September 9, 2026 - Regular Letting</a>
<a href="/indot/.../letting-archives2/future-2/">Wednesday, September 9, 2026 - Regular Letting (dup)</a>
<a href="/indot/.../letting-archives2/future-3/">Wednesday, September 16, 2026 - Regular Letting</a>
<a href="/indot/.../letting-archives2/future-4/">Wednesday, September 23, 2026 - Regular Letting</a>
<a href="/indot/.../letting-archives2/future-5-excluded/">Wednesday, September 30, 2026 - Regular Letting (excluded by cap)</a>
<a href="/indot/.../letting-archives2/future-6-excluded/">Wednesday, October 7, 2026 - Regular Letting (excluded by cap)</a>
</body></html>
'''

# NOTE: 1 today + 6 future = 7 upcoming candidates found locally; MAX_UPCOMING_LETTINGS=5
# keeps only the 5 EARLIEST (today, future-1, future-2, future-3, future-4) --
# future-5 and future-6 are excluded by the cap and must NEVER be fetched.
PAGE_HTML = {
    "today": '<html><body><a href="B-and-PH-List_today.pdf">Bidders</a></body></html>',
    "future-1": '<html><body><a href="B-and-PH-List_1.pdf">Bidders</a></body></html>',
    "future-2": '<html><body>No planholder link posted yet</body></html>',
    "future-3": '<html><body><a href="bidders_list_3.pdf">Bidders</a></body></html>',
    "future-4": '<html><body><a href="B-and-PH-List_4.pdf">Bidders</a></body></html>',
}

request_log = []

def counting_fetch_with_cache(url, force_refresh=False, timeout=30):
    request_log.append(url)
    if lc.is_archive_index_url(url):
        return ARCHIVE_HTML.encode(), {"source_url": url}
    if "excluded" in url or "past-" in url:
        raise AssertionError(f"MUST NOT FETCH (excluded by cap or by local date filtering): {url}")
    for key, html in PAGE_HTML.items():
        if key in url:
            return html.encode(), {"source_url": url}
    raise AssertionError(f"unexpected URL requested: {url}")


print("\n" + "=" * 78)
print("TEST (CRITICAL): request-bounded discover_upcoming_lettings()")
print("=" * 78)
request_log.clear()
with patch.object(lc, "fetch_with_cache", side_effect=counting_fetch_with_cache):
    results, index_entry = lc.discover_upcoming_lettings(today=date(2026, 8, 24))

archive_requests = sum(1 for u in request_log if lc.is_archive_index_url(u))
letting_page_requests = len(request_log) - archive_requests
pdf_requests = sum(1 for u in request_log if u.lower().endswith(".pdf"))

print(f"total requests made: {len(request_log)}")
print(f"archive requests: {archive_requests}")
print(f"letting-page requests: {letting_page_requests}")
print(f"PDF requests: {pdf_requests}")

assert archive_requests == 1, archive_requests
print("PASS: archive fetched exactly once")
assert letting_page_requests <= lc.MAX_UPCOMING_LETTINGS, letting_page_requests
print(f"PASS: letting-page requests ({letting_page_requests}) <= MAX_UPCOMING_LETTINGS ({lc.MAX_UPCOMING_LETTINGS})")
assert pdf_requests == 0, pdf_requests
print("PASS: zero PDF requests")

assert len(results) == lc.MAX_UPCOMING_LETTINGS, len(results)
print(f"PASS: exactly {lc.MAX_UPCOMING_LETTINGS} results returned")

dates = [r["letting_date"] for r in results]
assert dates == sorted(dates), dates
print(f"PASS: sorted ascending -> {dates}")
assert all(d >= "2026-08-24" for d in dates)
print("PASS: no past letting present, today's letting included")
assert "2026-09-30" not in dates and "2026-10-07" not in dates
print("PASS: future-5 and future-6 (6th and 7th candidates) correctly excluded by the cap")

# no page beyond the selected 5 was ever fetched -- the AssertionError inside the
# mock for future-5/future-6/past-* would have propagated and failed this test
# already; reaching this line IS the proof.
print("PASS: no page beyond the selected 5 was fetched (mock would have raised otherwise)")

dup_urls = [u for u in request_log if "future-2" in u]
assert len(dup_urls) <= 1, dup_urls
print("PASS: duplicate future-2 link fetched at most once (deduped before any request)")

future2 = next(r for r in results if "future-2" in r["letting_page_url"])
assert future2["planholder_list_available"] is False
assert future2["reason"] == "no_prebid_candidate_list"
print("PASS: Planholder link correctly detected as absent for future-2")

future1 = next(r for r in results if "future-1" in r["letting_page_url"])
assert future1["planholder_list_available"] is True
assert future1["planholder_url"].endswith("B-and-PH-List_1.pdf")
print(f"PASS: Planholder link discovered only after its page was fetched -> {future1['planholder_url']}")

# ============================================================
# STAGE 2: ingest_earliest_planholder_list() -- validated against the REAL,
# already-verified July 8, 2026 Planholder PDF held locally in this project
# (not a fresh live fetch -- see final report). Confirms the Stage-2 logic
# reproduces the previously-verified 238/238 candidate count exactly.
# ============================================================
import hashlib
from pathlib import Path

REAL_PDF = PROJECT_DIR / "bidders_list_07_08_2026.pdf"

if REAL_PDF.exists():
    print("\n" + "=" * 78)
    print("TEST: ingest_earliest_planholder_list() against the real, verified 2026 PDF")
    print("=" * 78)
    real_bytes = REAL_PDF.read_bytes()
    real_checksum = hashlib.sha256(real_bytes).hexdigest()
    fake_results = [{
        "letting_date": "2026-07-08",
        "letting_page_url": "https://www.in.gov/indot/.../wednesday,-july-8,-2026-regular-letting/",
        "planholder_list_available": True,
        "planholder_url": "https://www.in.gov/indot/doing-business-with-indot/files/Bidders-List_7.1.pdf",
    }]
    call_log = []

    def fake_fetch(url, force_refresh=False, timeout=30):
        call_log.append(url)
        return real_bytes, {
            "source_url": url, "retrieval_timestamp": "2026-08-24T00:00:00+00:00",
            "checksum": real_checksum, "changed_this_fetch": len(call_log) == 1,
            "local_path": str(REAL_PDF),
        }

    with patch.object(lc, "discover_upcoming_lettings", return_value=(fake_results, {})), \
         patch.object(lc, "fetch_with_cache", side_effect=fake_fetch):
        report = lc.ingest_earliest_planholder_list()
        report2 = lc.ingest_earliest_planholder_list()

    assert report["status"] == "ok"
    assert report["pdf_size_bytes"] == len(real_bytes) > 0
    assert report["candidate_count"] == 238, report["candidate_count"]
    assert report["valid_for_bid_count"] == 238, report["valid_for_bid_count"]
    print(f"PASS: candidate_count=238, valid_for_bid_count=238 -- matches the previously verified result exactly")
    assert report["pdf_sha256"] == report2["pdf_sha256"]
    assert report["changed_this_fetch"] is True and report2["changed_this_fetch"] is False
    print("PASS: refresh test -- identical checksum, changed_this_fetch True then False")
    assert len(call_log) == 2 and all("Bidders-List_7.1.pdf" in u for u in call_log)
    print("PASS: only the one real Planholder URL was ever requested (no unrelated document fetched)")
else:
    print("\n(skipping real-PDF Stage 2 test -- bidders_list_07_08_2026.pdf not present in this checkout)")

print("\n" + "=" * 78)
print("TEST: ingest_earliest_planholder_list() edge cases")
print("=" * 78)
with patch.object(lc, "discover_upcoming_lettings", return_value=([], {})):
    r = lc.ingest_earliest_planholder_list()
    assert r == {"status": "no_upcoming_letting"}, r
    print("PASS: no upcoming lettings -> status='no_upcoming_letting'")

no_bph_results = [{
    "letting_date": "2026-09-02", "letting_page_url": "https://www.in.gov/indot/.../sept2/",
    "planholder_list_available": False, "planholder_url": None, "reason": "no_prebid_candidate_list",
}]
with patch.object(lc, "discover_upcoming_lettings", return_value=(no_bph_results, {})):
    r = lc.ingest_earliest_planholder_list()
    assert r["status"] == "no_prebid_candidate_list", r
    print("PASS: earliest upcoming letting has no Planholder List -> status='no_prebid_candidate_list' (no old PDF substituted)")

bad_pdf_results = [{
    "letting_date": "2026-09-02", "letting_page_url": "https://www.in.gov/indot/.../sept2/",
    "planholder_list_available": True, "planholder_url": "https://www.in.gov/indot/.../not_a_pdf.pdf",
}]
with patch.object(lc, "discover_upcoming_lettings", return_value=(bad_pdf_results, {})), \
     patch.object(lc, "fetch_with_cache", return_value=(b"<html>not a pdf</html>", {"source_url": "x", "checksum": "x", "changed_this_fetch": True, "local_path": "x"})):
    r = lc.ingest_earliest_planholder_list()
    assert r["status"] == "invalid_pdf", r
    print("PASS: non-%PDF- bytes -> status='invalid_pdf'")

# ============================================================
# STAGE 3: run_live_pipeline() -- the full diagram, chained through to the
# FROZEN inference.infer(). Validated against the real R-43365-A candidate
# field, cross-checked against the exact result from a prior manual round.
# ============================================================
if REAL_PDF.exists():
    print("\n" + "=" * 78)
    print("TEST: run_live_pipeline() -- honest EE gap, then happy path")
    print("=" * 78)
    real_checksum2 = hashlib.sha256(REAL_PDF.read_bytes()).hexdigest()
    fake_ingest_ok = {
        "status": "ok", "letting_date": "2026-07-08", "letting_page_url": "x",
        "planholder_url": "https://www.in.gov/indot/doing-business-with-indot/files/Bidders-List_7.1.pdf",
        "pdf_size_bytes": REAL_PDF.stat().st_size, "pdf_sha256": real_checksum2,
        "retrieval_timestamp": "2026-08-24T00:00:00+00:00", "changed_this_fetch": True,
        "candidate_count": 238, "valid_for_bid_count": 238, "local_path": str(REAL_PDF),
    }

    with patch.object(lc, "ingest_earliest_planholder_list", return_value=fake_ingest_ok):
        result = lc.run_live_pipeline("R-43365-A", "35-0844079", 3229714.90)
    assert result["status"] == "ee_not_available_prebid", result
    print("PASS: correctly refuses to fabricate/borrow EE when no verified pre-bid source exists")

    import parse_indot as _pi
    _real_full_text = _pi.full_text
    def _patched_full_text(path):
        txt = _real_full_text(path)
        return txt.replace("R -43365-A", "R -43365-A\nEngineer's Estimate: $3,588,572.11", 1)

    with patch.object(lc, "ingest_earliest_planholder_list", return_value=fake_ingest_ok), \
         patch.object(lc, "full_text", side_effect=_patched_full_text):
        result2 = lc.run_live_pipeline("R-43365-A", "35-0844079", 3229714.90)
    assert result2["status"] == "ok"
    assert 0.0 <= result2["p_win"] <= 1.0
    assert result2["n_valid_candidates"] == 9
    assert abs(result2["p_win"] - 0.999499) < 1e-5, result2["p_win"]
    print(f"PASS: with EE available, reaches frozen inference -> p_win={result2['p_win']:.6f} "
          f"(matches the prior manually-validated R-43365-A result exactly)")
else:
    print("\n(skipping Stage 3 pipeline test -- bidders_list_07_08_2026.pdf not present in this checkout)")

print("\n" + "=" * 78)
print("ALL live_connector TESTS PASSED (no network access used)")
print("=" * 78)
