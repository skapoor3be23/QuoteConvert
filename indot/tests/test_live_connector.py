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

print("\n" + "=" * 78)
print("ALL live_connector TESTS PASSED (no network access used)")
print("=" * 78)
