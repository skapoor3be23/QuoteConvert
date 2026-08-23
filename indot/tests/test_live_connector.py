import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from datetime import date
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

print("\n" + "=" * 78)
print("ALL live_connector TESTS PASSED (no network access used)")
print("=" * 78)
