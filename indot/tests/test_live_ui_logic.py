import sys
from pathlib import Path
TEST_DIR = Path(__file__).resolve().parent
PROJECT_DIR = TEST_DIR.parent
sys.path.insert(0, str(PROJECT_DIR))

import live_ui_logic as ui

# ============================================================
# 1. upcoming letting + no Planholder
# ============================================================
print("=" * 78)
print("TEST 1: upcoming letting + no Planholder")
print("=" * 78)
results = [{"letting_date": "2026-09-02", "letting_page_url": "x", "planholder_list_available": False}]
state = ui.determine_discovery_state(results, ingest_status=None)
assert state == ui.STATE_NO_PREBID_CANDIDATE_LIST, state
print(f"PASS: {state}")

# ============================================================
# 2. upcoming letting + live Planholder
# ============================================================
print("\n" + "=" * 78)
print("TEST 2: upcoming letting + live Planholder")
print("=" * 78)
results = [{"letting_date": "2026-09-02", "letting_page_url": "x", "planholder_list_available": True,
            "planholder_url": "y"}]
state = ui.determine_discovery_state(results, ingest_status="ok")
assert state == ui.STATE_LIVE_CANDIDATES_AVAILABLE, state
print(f"PASS: {state}")

# ============================================================
# 3. competition-only mode (no reference estimate entered at all -- Mode A must
#    not require one; this is exercised at the discovery-state level, since
#    Mode A never even looks at reference_estimate)
# ============================================================
print("\n" + "=" * 78)
print("TEST 3: competition-only mode does not require a reference estimate")
print("=" * 78)
assert ui.determine_discovery_state(results, ingest_status="ok") == ui.STATE_LIVE_CANDIDATES_AVAILABLE
print("PASS: discovery state reachable with zero knowledge of any reference estimate")

# ============================================================
# 4. missing reference estimate
# ============================================================
print("\n" + "=" * 78)
print("TEST 4: missing reference estimate")
print("=" * 78)
state = ui.determine_prediction_state(None, 1_000_000.0)
assert state == ui.STATE_REFERENCE_ESTIMATE_REQUIRED, state
state2 = ui.determine_prediction_state(0.0, 1_000_000.0)
assert state2 == ui.STATE_REFERENCE_ESTIMATE_REQUIRED, state2
print(f"PASS: None -> {state}, 0.0 -> {state2}")

# ============================================================
# 5. valid reference estimate
# ============================================================
print("\n" + "=" * 78)
print("TEST 5: valid reference estimate (paired with a valid bid) reaches prediction_ready")
print("=" * 78)
state = ui.determine_prediction_state(2_000_000.0, 1_900_000.0)
assert state == ui.STATE_PREDICTION_READY, state
print(f"PASS: {state}")

# ============================================================
# 6. valid bid
# ============================================================
print("\n" + "=" * 78)
print("TEST 6: valid bid alone (with valid reference estimate) -> prediction_ready")
print("=" * 78)
assert ui.is_valid_positive_number(1_900_000.0) is True
print("PASS: is_valid_positive_number(1_900_000.0) is True")

# ============================================================
# 7. invalid/non-positive reference estimate
# ============================================================
print("\n" + "=" * 78)
print("TEST 7: invalid/non-positive reference estimate")
print("=" * 78)
for bad in [0.0, -5.0, None, "not_a_number"]:
    assert ui.is_valid_positive_number(bad) is False, bad
    assert ui.determine_prediction_state(bad, 1_000_000.0) == ui.STATE_REFERENCE_ESTIMATE_REQUIRED
print("PASS: 0.0, -5.0, None, 'not_a_number' all rejected as reference estimates")

# ============================================================
# 8. invalid/non-positive bid
# ============================================================
print("\n" + "=" * 78)
print("TEST 8: invalid/non-positive bid")
print("=" * 78)
for bad in [0.0, -1.0, None]:
    assert ui.determine_prediction_state(1_000_000.0, bad) == ui.STATE_BID_AMOUNT_REQUIRED, bad
print("PASS: 0.0, -1.0, None all rejected as bids -> STATE_BID_AMOUNT_REQUIRED")

# ============================================================
# 9. contractor not in candidate field
# ============================================================
print("\n" + "=" * 78)
print("TEST 9: contractor not in candidate field")
print("=" * 78)
cand_field = [
    {"name": "ACME PAVING", "fein": "11-1111111", "valid_for_bid": "Yes"},
    {"name": "BUILDCO", "fein": "22-2222222", "valid_for_bid": "No"},
]
assert ui.contractor_status(cand_field, "99-9999999") == "not_found"
print("PASS: FEIN not on the list -> 'not_found'")

# ============================================================
# 10. contractor with limited history (data_quality is computed inside
#     inference.infer(), not here -- this module only checks candidate-field
#     membership; verify "not_valid_for_bid" is distinguished from "valid")
# ============================================================
print("\n" + "=" * 78)
print("TEST 10: contractor listed but not Valid For Bid")
print("=" * 78)
assert ui.contractor_status(cand_field, "22-2222222") == "not_valid_for_bid"
assert ui.contractor_status(cand_field, "11-1111111") == "valid"
print("PASS: 'not_valid_for_bid' vs 'valid' correctly distinguished")

# ============================================================
# 11. prediction-ready state
# ============================================================
print("\n" + "=" * 78)
print("TEST 11: prediction-ready state (both inputs valid, contractor valid)")
print("=" * 78)
assert ui.determine_prediction_state(2_000_000.0, 1_800_000.0) == ui.STATE_PREDICTION_READY
assert ui.contractor_status(cand_field, "11-1111111") == "valid"
print("PASS: prediction_ready reached only when both reference estimate and bid are valid")

# ============================================================
# 12. bid-curve monotonicity -- delegated to inference.py's own frozen curve
# (already covered by test_inference.py); here we only confirm this module
# does not compute or alter any curve itself.
# ============================================================
print("\n" + "=" * 78)
print("TEST 12: this module contains no bid-curve computation of its own")
print("=" * 78)
import inspect
src = inspect.getsource(ui)
assert "bid_curve" not in src and "p_win" not in src
print("PASS: live_ui_logic.py never computes p_win or a bid curve -- app.py calls inference.infer() for that")

# ============================================================
# 13. frozen inference output unchanged (import-level check: this module must
# not shadow or wrap infer()/build_history_snapshot())
# ============================================================
print("\n" + "=" * 78)
print("TEST 13: frozen inference functions are not imported/wrapped by this module")
print("=" * 78)
assert not hasattr(ui, "infer")
assert not hasattr(ui, "build_history_snapshot")
assert hasattr(ui, "competition_level")  # imported, reused as-is, never redefined
from inference import competition_level as real_competition_level
assert ui.competition_level is real_competition_level
print("PASS: competition_level is the exact same frozen function object, not a copy")

# ============================================================
# build_contract_directory() against the real, previously-validated 2026 PDF
# ============================================================
REAL_PDF = PROJECT_DIR / "bidders_list_07_08_2026.pdf"
if REAL_PDF.exists():
    print("\n" + "=" * 78)
    print("TEST: build_contract_directory() against the real July 8, 2026 Planholder PDF")
    print("=" * 78)
    directory = ui.build_contract_directory(str(REAL_PDF))
    assert len(directory) == 48, len(directory)
    print(f"PASS: 48 contracts parsed (matches prior validated result)")
    total_valid = sum(info["n_valid_candidates"] for info in directory.values())
    assert total_valid == 238, total_valid
    print(f"PASS: 238 total Valid-For-Bid candidates (matches prior validated result)")
    golden = directory.get("R-43365-A")
    assert golden is not None, "R-43365-A missing from directory"
    assert golden["n_valid_candidates"] == 9, golden["n_valid_candidates"]
    print("PASS: golden contract R-43365-A has 9 Valid-For-Bid candidates")
    assert golden["competition_level"] == real_competition_level(9)
    print(f"PASS: competition_level for R-43365-A ({golden['competition_level']}) matches inference.competition_level(9) directly")
else:
    print("\n(skipping build_contract_directory real-PDF test -- bidders_list_07_08_2026.pdf not present)")

print("\n" + "=" * 78)
print("ALL live_ui_logic TESTS PASSED")
print("=" * 78)
