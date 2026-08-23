import json
import sys
import subprocess
from pathlib import Path
from datetime import datetime, timezone, timedelta

TEST_DIR = Path(__file__).resolve().parent
PROJECT_DIR = TEST_DIR.parent
sys.path.insert(0, str(PROJECT_DIR))

import live_ui_logic as ui

REAL_PDF = PROJECT_DIR / "bidders_list_07_08_2026.pdf"

# ============================================================
# 1. valid live snapshot
# ============================================================
print("=" * 78)
print("TEST 1: valid live snapshot")
print("=" * 78)
directory = {
    "R-43365-A": {
        "candidates": [
            {"name": "PAUL H ROHE COMPANY INC", "fein": "35-0844079", "valid_for_bid": "Yes"},
            {"name": "SOME OTHER CO", "fein": "11-1111111", "valid_for_bid": "No"},
        ],
        "district": "Fort Wayne",
    },
}
now_iso = datetime.now(timezone.utc).isoformat()
public = ui.build_snapshot(now_iso, "https://www.in.gov/indot/.../letting-archives2/",
                            "2026-09-02", "https://www.in.gov/indot/.../sept2/",
                            ui.SNAPSHOT_STATUS_AVAILABLE,
                            planholder_url="https://www.in.gov/indot/.../Bidders-List.pdf",
                            planholder_retrieved_at=now_iso, planholder_sha256="abc123",
                            candidate_count=2, valid_for_bid_count=1, directory=directory, include_fein=False)
assert ui.validate_public_snapshot_schema(public) is True
assert ui.classify_app_state(public) == ui.APP_STATE_LIVE_CANDIDATES_AVAILABLE
print("PASS: valid snapshot passes schema validation and classifies as live_candidates_available")

# ============================================================
# 2. missing live snapshot
# ============================================================
print("\n" + "=" * 78)
print("TEST 2: missing live snapshot")
print("=" * 78)
assert ui.classify_app_state(None) == ui.APP_STATE_REFRESH_FAILED
print("PASS: None (file not found) -> refresh_failed")

# ============================================================
# 3. malformed snapshot
# ============================================================
print("\n" + "=" * 78)
print("TEST 3: malformed snapshot")
print("=" * 78)
for bad in [{}, {"snapshot_version": 1}, {"snapshot_version": 1, "retrieved_at": "x", "source": "INDOT",
                                           "letting": {"status": "not_a_real_status"}}]:
    assert ui.classify_app_state(bad) == ui.APP_STATE_REFRESH_FAILED, bad
print("PASS: empty dict, missing 'letting', and unrecognized status all -> refresh_failed")

try:
    ui.validate_public_snapshot_schema({"letting": {"status": "available", "contracts": "not_a_list"}})
    raise AssertionError("should have raised")
except ValueError as e:
    print(f"PASS: malformed contracts field raises ValueError: {e}")

# ============================================================
# 4. stale snapshot
# ============================================================
print("\n" + "=" * 78)
print("TEST 4: stale snapshot")
print("=" * 78)
old_iso = (datetime.now(timezone.utc) - timedelta(hours=48)).isoformat()
stale_public = dict(public)
stale_public["retrieved_at"] = old_iso
assert ui.is_snapshot_stale(old_iso, threshold_hours=24) is True
assert ui.classify_app_state(stale_public, freshness_threshold_hours=24) == ui.APP_STATE_STALE_SNAPSHOT
fresh_iso = datetime.now(timezone.utc).isoformat()
assert ui.is_snapshot_stale(fresh_iso, threshold_hours=24) is False
print("PASS: 48h-old snapshot -> stale_snapshot; freshly retrieved snapshot -> not stale")

# ============================================================
# 5. no_prebid_candidate_list
# ============================================================
print("\n" + "=" * 78)
print("TEST 5: no_prebid_candidate_list")
print("=" * 78)
no_bph = ui.build_snapshot(now_iso, "archive_url", "2026-09-02", "letting_url", ui.SNAPSHOT_STATUS_NO_PREBID)
assert ui.validate_public_snapshot_schema(no_bph) is True
assert ui.classify_app_state(no_bph) == ui.APP_STATE_NO_PREBID_CANDIDATE_LIST
print("PASS: status=no_prebid_candidate_list -> app state no_prebid_candidate_list")

# ============================================================
# 6. live_candidates_available
# ============================================================
print("\n" + "=" * 78)
print("TEST 6: live_candidates_available (already covered by TEST 1, re-verified via classify_app_state)")
print("=" * 78)
assert ui.classify_app_state(public) == ui.APP_STATE_LIVE_CANDIDATES_AVAILABLE
print("PASS")

no_upcoming = ui.build_snapshot(now_iso, "archive_url", None, None, ui.SNAPSHOT_STATUS_NO_UPCOMING)
assert ui.classify_app_state(no_upcoming) == ui.APP_STATE_NO_UPCOMING_LETTING
print("PASS: status=no_upcoming_letting -> app state no_upcoming_letting")

# ============================================================
# 7. candidate directory parsing (via build_snapshot against real PDF-derived directory)
# ============================================================
print("\n" + "=" * 78)
print("TEST 7: candidate directory parsing feeds correctly into build_snapshot()")
print("=" * 78)
if REAL_PDF.exists():
    real_directory = ui.build_contract_directory(str(REAL_PDF))
    real_public = ui.build_snapshot(now_iso, "archive_url", "2026-07-08", "letting_url",
                                     ui.SNAPSHOT_STATUS_AVAILABLE, planholder_url="x",
                                     planholder_retrieved_at=now_iso, planholder_sha256="y",
                                     candidate_count=238, valid_for_bid_count=238,
                                     directory=real_directory, include_fein=False)
    ui.validate_public_snapshot_schema(real_public)
    assert len(real_public["letting"]["contracts"]) == 48
    golden = next(c for c in real_public["letting"]["contracts"] if c["contract_id"] == "R-43365-A")
    assert len(golden["valid_for_bid"]) == 9
    assert all(set(v.keys()) == {"contractor_name"} for v in golden["valid_for_bid"])
    print("PASS: real 48-contract PDF -> 48 contract entries; R-43365-A has 9 valid_for_bid entries, name-only")
else:
    print("(skipping -- bidders_list_07_08_2026.pdf not present)")

# ============================================================
# 8. contractor membership (delegates to contractor_status, already unit-tested
# in test_live_ui_logic.py -- here we verify it against the INTERNAL snapshot shape)
# ============================================================
print("\n" + "=" * 78)
print("TEST 8: contractor membership against the internal (FEIN-bearing) snapshot shape")
print("=" * 78)
internal = ui.build_snapshot(now_iso, "archive_url", "2026-09-02", "letting_url",
                              ui.SNAPSHOT_STATUS_AVAILABLE, planholder_url="x", planholder_retrieved_at=now_iso,
                              planholder_sha256="y", candidate_count=2, valid_for_bid_count=1,
                              directory=directory, include_fein=True)
contract0 = internal["letting"]["contracts"][0]
assert "fein" in contract0["valid_for_bid"][0]
assert "district" in contract0 and "all_candidates" in contract0
assert ui.contractor_status(contract0["all_candidates"], "35-0844079") == "valid"
assert ui.contractor_status(contract0["all_candidates"], "11-1111111") == "not_valid_for_bid"
assert ui.contractor_status(contract0["all_candidates"], "99-9999999") == "not_found"
print("PASS: internal snapshot carries enough (FEIN + full candidate list) to run contractor_status() correctly")

# ============================================================
# 9. no FEIN/PII exposed (in the PUBLIC snapshot only)
# ============================================================
print("\n" + "=" * 78)
print("TEST 9: no FEIN/PII exposed in the public snapshot")
print("=" * 78)
assert ui._scan_for_forbidden_keys(public, ui.PUBLIC_FORBIDDEN_KEYS) is None
print("PASS: public snapshot contains no fein/phone/email/address/ssn key anywhere")
# and the validator actively rejects one that does
poisoned = json.loads(json.dumps(public))
poisoned["letting"]["contracts"][0]["valid_for_bid"][0]["fein"] = "35-0844079"
try:
    ui.validate_public_snapshot_schema(poisoned)
    raise AssertionError("should have raised on a FEIN leaking into the public snapshot")
except ValueError as e:
    print(f"PASS: a FEIN key injected into the public snapshot is caught by the validator: {e}")
assert ui.classify_app_state(poisoned) == ui.APP_STATE_REFRESH_FAILED
print("PASS: a poisoned public snapshot is also rejected by classify_app_state -> refresh_failed (fails safe)")
# confirm the internal snapshot is NOT run through the public validator in generate_snapshot.py
# (spot-checked directly: internal snapshots legitimately contain 'fein')
assert ui._scan_for_forbidden_keys(internal, ui.PUBLIC_FORBIDDEN_KEYS) == "fein"
print("PASS: internal snapshot legitimately contains 'fein' (by design, never rendered to the UI)")

# ============================================================
# 10. unchanged snapshot does not create a commit
# ============================================================
print("\n" + "=" * 78)
print("TEST 10: unchanged snapshot content does not create a commit (workflow logic, simulated)")
print("=" * 78)
import tempfile, os as _os
with tempfile.TemporaryDirectory() as tmp:
    repo = Path(tmp)
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "a@b.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=repo, check=True)
    (repo / "data").mkdir()
    snap_path = repo / "data" / "live_snapshot.json"
    snap_path.write_text(json.dumps(public, indent=2), encoding="utf-8")
    subprocess.run(["git", "add", "data/live_snapshot.json"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "initial"], cwd=repo, check=True)
    before = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True).stdout.strip()

    # simulate a refresh that regenerates byte-identical content
    snap_path.write_text(json.dumps(public, indent=2), encoding="utf-8")
    subprocess.run(["git", "add", "data/live_snapshot.json"], cwd=repo, check=True)
    diff = subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=repo)
    assert diff.returncode == 0, "expected no staged diff for identical content"
    after = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True).stdout.strip()
    assert before == after
    print("PASS: `git diff --cached --quiet` correctly reports no change for byte-identical regenerated content "
          "-- this is exactly the condition indot_live_refresh.yml checks before committing")

# ============================================================
# 11. failed refresh does not overwrite previous snapshot
# ============================================================
print("\n" + "=" * 78)
print("TEST 11: failed refresh does not overwrite the previous snapshot")
print("=" * 78)
from unittest.mock import patch
import tempfile as _tempfile

with _tempfile.TemporaryDirectory() as tmp2:
    tmp2 = Path(tmp2)
    (tmp2 / "data").mkdir()
    public_path = tmp2 / "data" / "live_snapshot.json"
    internal_path = tmp2 / "data" / "live_snapshot_internal.json"
    previous_content = json.dumps({"marker": "PREVIOUS_GOOD_SNAPSHOT"})
    public_path.write_text(previous_content, encoding="utf-8")
    internal_path.write_text(previous_content, encoding="utf-8")

    import generate_snapshot as gs
    with patch.object(gs, "DATA_DIR", str(tmp2 / "data")), \
         patch.object(gs, "PUBLIC_PATH", str(public_path)), \
         patch.object(gs, "INTERNAL_PATH", str(internal_path)), \
         patch.object(gs, "discover_upcoming_lettings", side_effect=RuntimeError("simulated network failure")):
        rc = gs.main()

    assert rc == 1, f"expected exit code 1 on failure, got {rc}"
    assert public_path.read_text(encoding="utf-8") == previous_content
    assert internal_path.read_text(encoding="utf-8") == previous_content
    print("PASS: discovery raising an exception -> main() returns 1, and both snapshot files are byte-identical "
          "to what existed before the failed run (never overwritten with partial/empty data)")

    # same check for an ingest-stage failure (invalid_pdf) after discovery succeeds
    fake_results = [{
        "letting_date": "2026-09-02", "letting_page_url": "https://www.in.gov/x/",
        "planholder_list_available": True, "planholder_url": "https://www.in.gov/x/bad.pdf",
    }]
    with patch.object(gs, "DATA_DIR", str(tmp2 / "data")), \
         patch.object(gs, "PUBLIC_PATH", str(public_path)), \
         patch.object(gs, "INTERNAL_PATH", str(internal_path)), \
         patch.object(gs, "discover_upcoming_lettings", return_value=(fake_results, {})), \
         patch.object(gs, "ingest_earliest_planholder_list", return_value={"status": "invalid_pdf"}):
        rc2 = gs.main()

    assert rc2 == 1
    assert public_path.read_text(encoding="utf-8") == previous_content
    assert internal_path.read_text(encoding="utf-8") == previous_content
    print("PASS: an invalid_pdf ingest result also -> exit 1, previous snapshot files left untouched")

print("\n" + "=" * 78)
print("ALL test_snapshot TESTS PASSED")
print("=" * 78)
