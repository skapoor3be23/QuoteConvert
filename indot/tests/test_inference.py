import sys
from pathlib import Path

TEST_DIR = Path(__file__).resolve().parent
PROJECT_DIR = TEST_DIR.parent
DATA_DIR = PROJECT_DIR / "data"

sys.path.insert(0, str(PROJECT_DIR))
import json
import numpy as np
import pandas as pd
from inference import build_history_snapshot, infer, MODEL_ARTIFACT

df = pd.read_csv(DATA_DIR / "indot_dataset.csv")
df["letting_date"] = pd.to_datetime(df["letting_date"])
train = df[df.split == "train"]
TRAIN_GLOBAL_RATIOS = sorted(train.ratio.values)


def real_candidate_field(row):
    return json.loads(row.candidates_json)


def project_info_for(row):
    return {"engineers_estimate": row.engineers_estimate, "district": row.district}


# ============================================================
# helper to run one real historical contract "as of" its own letting date
# ============================================================
def run_case(row, bid_amount=None, fein=None):
    as_of = row.letting_date
    snap = build_history_snapshot(df, as_of, TRAIN_GLOBAL_RATIOS)
    cand_field = real_candidate_field(row)
    fein_ = fein if fein is not None else row.bidder_fein
    bid_amount_ = bid_amount if bid_amount is not None else row.bidder_total
    return infer(row.contract_id, fein_, bid_amount_, project_info_for(row), cand_field, snap)


test_df = df[df.split == "test"].reset_index(drop=True)

print("=" * 78)
print("TEST 1: reproducibility (run same query twice)")
print("=" * 78)
row = test_df.iloc[0]
r1 = run_case(row)
r2 = run_case(row)
assert r1 == r2, "reproducibility FAILED -- outputs differ across identical runs"
print("PASS -- identical output across two runs")
print(json.dumps(r1, indent=2)[:600])

print("\n" + "=" * 78)
print("TEST 2: q_final in [0,1], bid curve monotonic")
print("=" * 78)
assert 0.0 <= r1["p_win"] <= 1.0
prev = None
mono_ok = True
for pt in r1["bid_curve"]:
    if prev is not None and pt["p_win"] > prev + 1e-9:
        mono_ok = False
    prev = pt["p_win"]
print(f"p_win={r1['p_win']}  bid_curve monotonic (non-increasing)? {mono_ok}")
assert mono_ok, "MONOTONICITY FAILED"
print("PASS")

print("\n" + "=" * 78)
print("TEST 3: sum(q_final) approx 1 across the full candidate field for one contract")
print("=" * 78)
cid = test_df.contract_id.iloc[5]
sub = df[df.contract_id == cid]
as_of = sub.letting_date.iloc[0]
snap = build_history_snapshot(df, as_of, TRAIN_GLOBAL_RATIOS)
cand_field = real_candidate_field(sub.iloc[0])
valid_feins = [c["fein"] for c in cand_field if c["valid_for_bid"] == "Yes" and c["fein"]]
proj = project_info_for(sub.iloc[0])
total_q = 0.0
for fein in valid_feins:
    # use each candidate's actual observed ratio if they are an actual bidder in this test contract, else their own history mean
    actual_row = sub[sub.bidder_fein == fein]
    if len(actual_row):
        bid_amt = actual_row.bidder_total.iloc[0]
    else:
        prior = snap.fein_ratios.get(fein, [])
        bid_amt = (np.mean(prior) if len(prior) >= 3 else np.median(TRAIN_GLOBAL_RATIOS)) * proj["engineers_estimate"]
    res = infer(cid, fein, bid_amt, proj, cand_field, snap)
    if res["status"] == "ok":
        total_q += res["p_win"]
print(f"contract {cid}: sum(q_final) over {len(valid_feins)} candidates = {total_q:.4f}")
print("(expected ~1.0 when every candidate's OWN bid is used consistently; here we mix actual/imputed bids for absent bidders, so exact 1.0 is not guaranteed -- reported as a diagnostic, not a strict assertion)")

print("\n" + "=" * 78)
print("TEST 4 (LEAKAGE ASSERTIONS)")
print("=" * 78)
row = test_df.iloc[10]
as_of = row.letting_date
snap = build_history_snapshot(df, as_of, TRAIN_GLOBAL_RATIOS)
for fein, ratios_list in snap.fein_ratios.items():
    sub_hist = df[(df.bidder_fein == fein) & (df.letting_date < as_of)]
    assert len(ratios_list) == len(sub_hist), f"leakage: history length mismatch for {fein}"
print(f"PASS -- all {len(snap.fein_ratios)} contractor histories in snapshot contain ONLY rows strictly before {as_of.date()}")
assert MODEL_ARTIFACT["calibration_period"]["end"] < MODEL_ARTIFACT["test_period"]["start"]
print("PASS -- calibration period ends before test period starts")
assert "bidder_total" not in json.dumps(real_candidate_field(row))
print("PASS -- current candidate field contains no bid amounts (genuinely pre-bid fields only: fein, valid_for_bid)")

print("\n" + "=" * 78)
print("TEST 10 (HISTORICAL AS-OF TEST): infer succeeds using ONLY pre-bid info for a real past contract")
print("=" * 78)
row = test_df.iloc[20]
result = run_case(row)
print(json.dumps(result, indent=2))
assert result["status"] == "ok"
assert "actual_bidders" not in json.dumps(result) and "winner" not in json.dumps(result).lower()
print("PASS -- no post-bid fields present in output")

# ============================================================
# 12. END-TO-END TEST CASES
# ============================================================
print("\n" + "=" * 78)
print("END-TO-END TEST CASES")
print("=" * 78)

def show(name, result):
    print(f"\n--- {name} ---")
    print(json.dumps(result, indent=2)[:500])

# 1. Standard-history contractor
std_rows = test_df[test_df.contractor_prior_n >= 3]
r = run_case(std_rows.iloc[0])
show("1. Standard-history contractor", r)
assert r["data_quality"] in ("Standard", "Limited history")

# 2. Limited-history contractor
thin_rows = test_df[test_df.contractor_prior_n < 3]
r = run_case(thin_rows.iloc[0])
show("2. Limited-history contractor", r)
assert r["status"] == "ok"

# 3. No candidate list
row = test_df.iloc[0]
snap = build_history_snapshot(df, row.letting_date, TRAIN_GLOBAL_RATIOS)
r = infer(row.contract_id, row.bidder_fein, row.bidder_total, project_info_for(row), [], snap)
show("3. No candidate list", r)
assert r["status"] == "unavailable" and r["reason"] == "no_prebid_candidate_list"

# 4. Contractor absent from candidate field
r = infer(row.contract_id, "99-9999999", row.bidder_total, project_info_for(row), real_candidate_field(row), snap)
show("4. Contractor absent from candidate field", r)
assert r["status"] == "contractor_not_in_candidate_field"

# 5. Unusual EE project
unusual_rows = test_df.copy()
unusual_rows["log_ee"] = np.log(unusual_rows.engineers_estimate)
found_unusual = None
for _, rr in unusual_rows.iterrows():
    res = run_case(rr)
    if res.get("unusual_project"):
        found_unusual = res
        break
show("5. Unusual EE project", found_unusual if found_unusual else {"note": "no unusual-EE contract found in this test sample"})

# 6. Low bid
row = test_df.iloc[30]
r = run_case(row, bid_amount=0.80 * row.engineers_estimate)
show("6. Low bid (80% EE)", r)
assert r["status"] == "ok" or r["status"] == "contractor_not_in_candidate_field"

# 7. High bid
r = run_case(row, bid_amount=1.20 * row.engineers_estimate)
show("7. High bid (120% EE)", r)

print("\n" + "=" * 78)
print("ALL TESTS COMPLETED")
print("=" * 78)
