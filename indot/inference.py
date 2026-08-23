"""
QuoteConvert V1 inference engine.

Deterministic, leakage-safe P(win) inference for a single (contract, contractor, bid_amount)
query, built strictly from data known before the bid deadline.

This module never fits parameters -- it only APPLIES the frozen artifact in model_artifact.json
against a historical "as-of" snapshot of the data (built by build_history_snapshot). In a live
deployment, build_history_snapshot would be replaced by a live query against INDOT's current
Planholder List + this project's own accumulated bid-tabulation history, but the inference
logic itself (this file) does not change.
"""
import json
import numpy as np
import pandas as pd

import os
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
MODEL_ARTIFACT = json.load(open(os.path.join(_THIS_DIR, "data", "model_artifact.json")))
T = MODEL_ARTIFACT["temperature_T"]
P_GLOBAL = MODEL_ARTIFACT["global_participation_rate"]
OWN_MIN_OBS = MODEL_ARTIFACT["fallback_thresholds"]["contractor_own_history_min_obs"]
DISTRICT_MIN_OBS = MODEL_ARTIFACT["fallback_thresholds"]["district_pooled_min_obs"]
COMP_LOW_MAX = MODEL_ARTIFACT["competition_level_thresholds"]["low_max"]
COMP_MOD_MAX = MODEL_ARTIFACT["competition_level_thresholds"]["moderate_max"]
EE_THRESH = MODEL_ARTIFACT["unusual_project_thresholds"]["by_district"]
SANITY_MIN = MODEL_ARTIFACT["data_entry_sanity_check"]["min_ratio"]
SANITY_MAX = MODEL_ARTIFACT["data_entry_sanity_check"]["max_ratio"]


class HistorySnapshot:
    """Everything the model is allowed to see, frozen at a specific as-of date.

    fein_ratios: {fein: sorted list of strictly-prior ratios}
    fein_winrates: {fein: strictly-prior win rate (None if <3 obs)}
    fein_avgratios: {fein: strictly-prior avg ratio (None if <3 obs)}
    district_ratios: {district: sorted list of strictly-prior ratios, global pool}
    global_ratios: sorted list, training-period ratios (fixed, from the artifact's training window)
    """
    def __init__(self, fein_ratios, fein_winrates, fein_avgratios, district_ratios, global_ratios):
        self.fein_ratios = fein_ratios
        self.fein_winrates = fein_winrates
        self.fein_avgratios = fein_avgratios
        self.district_ratios = district_ratios
        self.global_ratios = global_ratios

    def survival_fn(self, fein, district):
        """Returns (fn, tier) where fn(r) = P(this competitor's ratio > r)."""
        prior = self.fein_ratios.get(fein, [])
        if len(prior) >= OWN_MIN_OBS:
            s = np.sort(prior)
            return (lambda r, s=s: 1 - np.searchsorted(s, r, side="right") / len(s)), "own"
        priord = self.district_ratios.get(district, [])
        if len(priord) >= DISTRICT_MIN_OBS:
            s = np.sort(priord)
            return (lambda r, s=s: 1 - np.searchsorted(s, r, side="right") / len(s)), "district"
        s = self.global_ratios
        return (lambda r, s=s: 1 - np.searchsorted(s, r, side="right") / len(s)), "global"

    def contractor_prior(self, fein):
        wr = self.fein_winrates.get(fein)
        ar = self.fein_avgratios.get(fein)
        n = len(self.fein_ratios.get(fein, []))
        return wr, ar, n


def build_history_snapshot(full_df, as_of_date, train_global_ratios):
    """Build a HistorySnapshot using ONLY rows with letting_date < as_of_date.
    This is the leakage boundary: everything downstream only sees this snapshot."""
    prior = full_df[pd.to_datetime(full_df.letting_date) < as_of_date]
    fein_ratios, fein_win_hist = {}, {}
    for fein, g in prior.groupby("bidder_fein"):
        fein_ratios[fein] = g.ratio.tolist()
        fein_win_hist[fein] = g.is_winner.tolist()
    fein_winrates = {f: (np.mean(fein_win_hist[f]) if len(fein_win_hist[f]) >= OWN_MIN_OBS else None) for f in fein_win_hist}
    fein_avgratios = {f: (np.mean(fein_ratios[f]) if len(fein_ratios[f]) >= OWN_MIN_OBS else None) for f in fein_ratios}
    district_ratios = {d: g.ratio.tolist() for d, g in prior.groupby("district")}
    return HistorySnapshot(fein_ratios, fein_winrates, fein_avgratios, district_ratios, train_global_ratios)


def competition_level(n_valid_candidates):
    if n_valid_candidates <= COMP_LOW_MAX:
        return "Low"
    if n_valid_candidates <= COMP_MOD_MAX:
        return "Moderate"
    return "High"


def unusual_project(engineers_estimate, district):
    if district not in EE_THRESH:
        return False  # unknown district -> cannot evaluate, treated as not-flagged (data_quality should already be Unavailable in this case)
    log_ee = np.log(engineers_estimate)
    band = EE_THRESH[district]
    return not (band["p5"] <= log_ee <= band["p95"])


def _raw_p(ratio, other_candidates, snapshot, district):
    """P_raw for a candidate bidding at `ratio`, given the OTHER real candidates."""
    total = 1.0
    for fein in other_candidates:
        sfn, _tier = snapshot.survival_fn(fein, district)
        s = sfn(ratio)
        total *= ((1 - P_GLOBAL) + P_GLOBAL * s)
    return total


def infer(contract_id, contractor_fein, bid_amount, project_info, candidate_field, snapshot):
    """
    project_info: {"engineers_estimate": float, "district": str}
    candidate_field: list of {"fein": str, "valid_for_bid": "Yes"/"No"} -- the CURRENT
                      pre-bid Planholder List for this contract (must include contractor_fein
                      itself if they are a registered candidate).
    snapshot: HistorySnapshot built strictly before this contract's bid deadline.
    """
    reasons = []

    ee = project_info.get("engineers_estimate")
    district = project_info.get("district")
    if ee is None or ee <= 0 or district is None:
        return {"status": "unavailable", "reason": "missing_project_information", "model_version": MODEL_ARTIFACT["model_version"]}

    if candidate_field is None or len(candidate_field) == 0:
        return {"status": "unavailable", "reason": "no_prebid_candidate_list", "model_version": MODEL_ARTIFACT["model_version"]}

    valid_feins = [c["fein"] for c in candidate_field if c.get("valid_for_bid") == "Yes" and c.get("fein")]
    if contractor_fein not in valid_feins:
        return {"status": "contractor_not_in_candidate_field",
                "reason": "the requesting contractor is not on the current published Valid-For-Bid list for this contract",
                "model_version": MODEL_ARTIFACT["model_version"]}

    if bid_amount is None or bid_amount <= 0:
        return {"status": "unavailable", "reason": "invalid_bid_amount", "model_version": MODEL_ARTIFACT["model_version"]}

    ratio = bid_amount / ee
    sanity_warning = not (SANITY_MIN <= ratio <= SANITY_MAX)

    other_candidates = [f for f in valid_feins if f != contractor_fein]

    # data quality
    own_wr, own_ar, own_n = snapshot.contractor_prior(contractor_fein)
    tiers_used = []
    for f in other_candidates:
        _, tier = snapshot.survival_fn(f, district)
        tiers_used.append(tier)
    focal_standard = own_n >= OWN_MIN_OBS
    others_standard = all(t == "own" for t in tiers_used) if tiers_used else True
    if focal_standard and others_standard:
        data_quality = "Standard"
    else:
        data_quality = "Limited history"

    def q_final_for_ratio(r):
        p_focal = _raw_p(r, other_candidates, snapshot, district)
        p_others = {f: _raw_p(
            (np.mean(snapshot.fein_ratios[f]) if len(snapshot.fein_ratios.get(f, [])) >= OWN_MIN_OBS
             else np.median(snapshot.global_ratios)),
            [x for x in valid_feins if x != f], snapshot, district
        ) for f in other_candidates}
        vals = {contractor_fein: p_focal, **p_others}
        temp = {k: v ** (1.0 / T) for k, v in vals.items()}
        z = sum(temp.values())
        if z <= 0:
            return 0.0
        return temp[contractor_fein] / z

    p_win = q_final_for_ratio(ratio)

    curve = []
    for delta in [-0.15, -0.10, -0.05, 0.00, 0.05, 0.10, 0.15]:
        r = ratio + delta
        if r <= 0:
            continue
        curve.append({
            "ratio": round(r, 4),
            "bid_amount": round(r * ee, 2),
            "p_win": round(q_final_for_ratio(r), 6),
            "outside_sanity_range": not (SANITY_MIN <= r <= SANITY_MAX),
        })

    n_valid = len(valid_feins)
    result = {
        "status": "ok",
        "contract_id": contract_id,
        "contractor_fein": contractor_fein,
        "bid_amount": round(bid_amount, 2),
        "engineers_estimate": round(ee, 2),
        "bid_ratio": round(ratio, 4),
        "p_win": round(p_win, 6),
        "bid_curve": curve,
        "competition_level": competition_level(n_valid),
        "n_valid_candidates": n_valid,
        "data_quality": data_quality,
        "unusual_project": unusual_project(ee, district),
        "data_entry_warning": sanity_warning,
        "model_version": MODEL_ARTIFACT["model_version"],
    }
    return result
