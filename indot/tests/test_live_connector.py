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

r3 = lc.resolve_url("https://www.in.gov/anything/", "https://www.in.gov/indot/files/x.pdf")
assert r3 == "https://www.in.gov/indot/files/x.pdf"
print("PASS (already-absolute link unchanged):", r3)

print("\n" + "=" * 78)
print("TEST: spaced contract ID normalization (reused from parse_indot, not duplicated)")
print("=" * 78)
from parse_indot import CONTRACT_RE
m = CONTRACT_RE.search("R -43365-A")
assert m is not None
normalized = m.group(0).replace(" ", "")
assert normalized == "R-43365-A", normalized
print(f"PASS: 'R -43365-A' -> '{normalized}'")

print("\n" + "=" * 78)
print("TEST: no-planholder state")
print("=" * 78)
html_no_pdf = '<html><body><a href="/indot/files/NoticeToBidders.pdf">Notice</a></body></html>'
link = lc.find_planholder_link(html_no_pdf)
assert link is None
print("PASS: no Planholder-pattern PDF link -> None (would yield reason='no_prebid_candidate_list')")

html_with_pdf = '<html><body><a href="/indot/files/B-and-PH-List_9.2.pdf">Bidders List</a></body></html>'
link2 = lc.find_planholder_link(html_with_pdf)
assert link2 == "/indot/files/B-and-PH-List_9.2.pdf"
print(f"PASS: real Planholder pattern found -> {link2}")

print("\n" + "=" * 78)
print("TEST: upcoming vs past letting date")
print("=" * 78)
today = date(2026, 8, 24)
past_text = "INDOT Doing Business with INDOT: Wednesday, July 8, 2026 - Regular Letting"
future_text = "INDOT Doing Business with INDOT: Wednesday, September 2, 2026 - Regular Letting"
past_date = lc.extract_letting_date(past_text)
future_date = lc.extract_letting_date(future_text)
assert past_date == date(2026, 7, 8), past_date
assert future_date == date(2026, 9, 2), future_date
assert lc.is_upcoming(past_date, today=today) is False
assert lc.is_upcoming(future_date, today=today) is True
assert lc.is_upcoming(None, today=today) is False
print(f"PASS: {past_date} (past) -> upcoming={lc.is_upcoming(past_date, today=today)}")
print(f"PASS: {future_date} (future) -> upcoming={lc.is_upcoming(future_date, today=today)}")
print(f"PASS: no date found -> upcoming=False (never guessed)")

print("\n" + "=" * 78)
print("TEST: duplicate letting URL deduplication")
print("=" * 78)
urls = [
    "https://www.in.gov/indot/doing-business-with-indot/home/contracts/letting-archives2/wednesday,-september-2,-2026-regular-letting/",
    "https://www.in.gov/indot/doing-business-with-indot/home/contracts/letting-archives2/wednesday,-september-2,-2026-regular-letting",  # no trailing slash
    "https://WWW.IN.GOV/indot/doing-business-with-indot/home/contracts/letting-archives2/wednesday,-september-2,-2026-regular-letting/",  # different host case
    "https://www.in.gov/indot/doing-business-with-indot/home/contracts/letting-archives2/wednesday,-april-8,-2026-regular-letting/",
]
deduped = lc.dedupe_urls(urls)
assert len(deduped) == 2, deduped
print(f"PASS: {len(urls)} input URLs (3 equivalent + 1 distinct) -> {len(deduped)} after dedup")

print("\n" + "=" * 78)
print("TEST: archive index safety (index page never treated as a letting page)")
print("=" * 78)
assert lc.is_archive_index_url(lc.LETTING_ARCHIVE_URL) is True
assert lc.is_archive_index_url(lc.LETTING_ARCHIVE_URL.rstrip("/")) is True
assert lc.is_archive_index_url(
    "https://www.in.gov/indot/doing-business-with-indot/home/contracts/letting-archives2/wednesday,-april-8,-2026-regular-letting/"
) is False
print("PASS")

# ============================================================
# bounded discover_upcoming_lettings() tests -- all network calls mocked,
# genuinely network-free, exercising the real Stage-1 logic end to end.
# ============================================================

ARCHIVE_HTML = '''
<html><body>
<a href="/indot/doing-business-with-indot/home/contracts/letting-archives2/">letting-archives2</a>
<a href="/indot/doing-business-with-indot/home/contracts/letting-archives2/wednesday,-past-letting/">Past</a>
<a href="/indot/doing-business-with-indot/home/contracts/letting-archives2/wednesday,-future-a-letting/">Future A</a>
<a href="/indot/doing-business-with-indot/home/contracts/letting-archives2/wednesday,-future-a-letting">Future A dup</a>
<a href="/indot/doing-business-with-indot/home/contracts/letting-archives2/wednesday,-future-b-letting/">Future B</a>
<a href="/indot/doing-business-with-indot/home/contracts/letting-archives2/wednesday,-future-c-letting/">Future C</a>
<a href="/indot/doing-business-with-indot/home/contracts/letting-archives2/wednesday,-future-d-letting/">Future D</a>
<a href="/indot/doing-business-with-indot/home/contracts/letting-archives2/wednesday,-future-e-letting/">Future E</a>
<a href="/indot/doing-business-with-indot/home/contracts/letting-archives2/wednesday,-future-f-letting/">Future F (should be excluded, only 5 allowed)</a>
<a href="/indot/doing-business-with-indot/home/contracts/letting-archives2/wednesday,-broken-letting/">Broken</a>
</body></html>
'''

PAGE_HTML = {
    "past-letting": '<html><body>Wednesday, July 1, 2020 - Regular Letting</body></html>',
    "future-a-letting": '<html><body>Wednesday, September 10, 2026 - Regular Letting <a href="B-and-PH-List_a.pdf">Bidders</a></body></html>',
    "future-b-letting": '<html><body>Wednesday, September 3, 2026 - Regular Letting</body></html>',  # no planholder link yet
    "future-c-letting": '<html><body>Wednesday, September 17, 2026 - Regular Letting <a href="bidders_list_c.pdf">Bidders</a></body></html>',
    "future-d-letting": '<html><body>Wednesday, September 24, 2026 - Regular Letting <a href="B-and-PH-List_d.pdf">Bidders</a></body></html>',
    "future-e-letting": '<html><body>Wednesday, October 1, 2026 - Regular Letting <a href="B-and-PH-List_e.pdf">Bidders</a></body></html>',
    "future-f-letting": '<html><body>Wednesday, October 8, 2026 - Regular Letting <a href="B-and-PH-List_f.pdf">Bidders</a></body></html>',
}


def fake_fetch_with_cache(url, force_refresh=False, timeout=30):
    if lc.is_archive_index_url(url):
        return ARCHIVE_HTML.encode(), {"source_url": url}
    if "broken-letting" in url:
        raise TimeoutError("simulated one bad letting page")
    for key, html in PAGE_HTML.items():
        if key in url:
            return html.encode(), {"source_url": url}
    raise AssertionError(f"unexpected URL in mock: {url}")


print("\n" + "=" * 78)
print("TEST: bounded discover_upcoming_lettings() -- fully mocked, no network")
print("=" * 78)
with patch.object(lc, "fetch_with_cache", side_effect=fake_fetch_with_cache):
    results, index_entry = lc.discover_upcoming_lettings(today=date(2026, 8, 24))

print(f"results returned: {len(results)}")
for r in results:
    print(" ", r)

assert len(results) == lc.MAX_UPCOMING_LETTINGS, f"expected exactly {lc.MAX_UPCOMING_LETTINGS}, got {len(results)}"
print(f"PASS: exactly {lc.MAX_UPCOMING_LETTINGS} results (max cap enforced, future-f-letting excluded)")

dates = [r["letting_date"] for r in results]
assert dates == sorted(dates), f"not sorted ascending: {dates}"
print(f"PASS: sorted ascending -> {dates}")

assert all(d >= "2026-08-24" for d in dates), "a past letting leaked into results"
print("PASS: no past letting present (past-letting correctly excluded)")

urls_seen = [r["letting_page_url"] for r in results]
assert len(urls_seen) == len(set(u.rstrip("/") for u in urls_seen)), "duplicate URL not removed"
print("PASS: duplicate future-a-letting URL (with/without trailing slash) collapsed to one entry")

b_result = next(r for r in results if "future-b-letting" in r["letting_page_url"])
assert b_result["planholder_list_available"] is False
assert b_result["reason"] == "no_prebid_candidate_list"
print("PASS: missing Planholder link -> planholder_list_available=False, reason='no_prebid_candidate_list'")

a_result = next(r for r in results if "future-a-letting" in r["letting_page_url"])
assert a_result["planholder_list_available"] is True
assert a_result["planholder_url"].endswith("B-and-PH-List_a.pdf")
print(f"PASS: found Planholder link resolved to absolute URL -> {a_result['planholder_url']}")

print("\n(one broken letting page was included in the mocked archive index; discovery completed")
print(" without raising, confirming one bad page does not stop the rest -- see 'broken-letting' handling)")

print("\n" + "=" * 78)
print("ALL live_connector TESTS PASSED (no network access used)")
print("=" * 78)
