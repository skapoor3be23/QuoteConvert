"""
QuoteConvert -- Local Demo V1

Runs the FROZEN inference engine (inference.py) against the validated historical
INDOT dataset in "as-of" mode: for a selected historical contract, the app rebuilds
exactly the information that would have been available BEFORE that contract's bid
deadline (contractor history strictly before the letting date, and that contract's
own real pre-bid Valid-For-Bid candidate list), then asks the frozen model for a
P(win) on a hypothetical bid the user enters.

No probability in this app is hard-coded or altered. Every number comes from
inference.infer(); this file only changes how those numbers are displayed.
"""
import json
import os
import streamlit as st
import pandas as pd
import numpy as np

import inference
from inference import build_history_snapshot, infer, MODEL_ARTIFACT

DATA_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "indot_dataset.csv")

st.set_page_config(page_title="QuoteConvert", page_icon="📐", layout="centered")


@st.cache_data
def load_dataset():
    df = pd.read_csv(DATA_PATH)
    df["letting_date"] = pd.to_datetime(df["letting_date"])
    train = df[df.split == "train"]
    global_ratios = sorted(train.ratio.values)
    # training-data-supported bid-ratio band (5th/95th percentile of TRAIN ratios only,
    # never test data) -- used only for the extreme-range display warning below.
    supported_band = (float(train.ratio.quantile(0.05)), float(train.ratio.quantile(0.95)))
    full_band = (float(train.ratio.quantile(0.01)), float(train.ratio.quantile(0.99)))
    contracts = (
        df.groupby("contract_id")
        .agg(letting_date=("letting_date", "first"), district=("district", "first"),
             county=("county", "first"), engineers_estimate=("engineers_estimate", "first"),
             n_candidates=("n_candidates_validyes", "first"))
        .reset_index()
        .sort_values("letting_date", ascending=False)
    )
    return df, global_ratios, contracts, supported_band, full_band


df, TRAIN_GLOBAL_RATIOS, contracts, SUPPORTED_BAND, FULL_BAND = load_dataset()

COMP_LOW_MAX = MODEL_ARTIFACT["competition_level_thresholds"]["low_max"]
COMP_MOD_MAX = MODEL_ARTIFACT["competition_level_thresholds"]["moderate_max"]


def competition_label(n_valid, level):
    return f"{level} competition — {n_valid} pre-bid candidate{'s' if n_valid != 1 else ''}"


# ------------------------------------------------------------------
st.title("QuoteConvert")
st.caption("Construction Bid Win Probability")
st.warning(
    "**DEMO MODE — Historical INDOT data.** Reconstructed strictly as it would have appeared "
    "*before* each contract's bid deadline. Live INDOT connectivity is not currently enabled."
)

mode = st.radio("Data mode", ["Demo (historical, as-of)", "Live (unavailable)"], horizontal=True, label_visibility="collapsed")
if mode == "Live (unavailable)":
    st.error("Live INDOT data unavailable. Switch to Demo mode to try the engine against historical as-of scenarios.")
    st.stop()

# ==================== TOP: PROJECT INFORMATION ====================
st.subheader("Project")

default_cid = "B-40989-A" if "B-40989-A" in contracts.contract_id.values else contracts.contract_id.iloc[0]
options = contracts.contract_id.tolist()
default_pos = options.index(default_cid) if default_cid in options else 0

selected_cid = st.selectbox(
    "Historical INDOT contract",
    options,
    index=default_pos,
    format_func=lambda cid: f"{cid} — {contracts.loc[contracts.contract_id==cid,'letting_date'].dt.date.values[0]}",
)

row = contracts[contracts.contract_id == selected_cid].iloc[0]
sub = df[df.contract_id == selected_cid]
cand_field = json.loads(sub.candidates_json.iloc[0])
valid_candidates = [c for c in cand_field if c.get("valid_for_bid") == "Yes" and c.get("fein")]

c1, c2, c3, c4 = st.columns(4)
c1.metric("Letting date", str(row.letting_date.date()))
c2.metric("District", row.district)
c3.metric("Engineer's Estimate", f"${row.engineers_estimate:,.0f}")
c4.metric("Pre-bid candidates", int(row.n_candidates))
st.caption(f"County: {row.county}  |  Contract ID: {selected_cid}")

if not valid_candidates:
    st.error("No pre-bid candidate list is available for this contract — QuoteConvert cannot "
             "produce a competition-aware estimate.")
    st.stop()

# ==================== MIDDLE: CONTRACTOR + BID ====================
st.divider()
st.subheader("Contractor & Bid")

cand_names = {c["fein"]: c.get("name", c["fein"]) for c in valid_candidates}
cc1, cc2 = st.columns([1, 1])
with cc1:
    selected_fein = st.selectbox(
        "Contractor (from this project's published pre-bid candidate list)",
        list(cand_names.keys()),
        format_func=lambda f: cand_names[f],
    )
with cc2:
    default_bid = float(row.engineers_estimate)
    bid_amount = st.number_input(
        "Hypothetical bid amount ($)",
        min_value=0.0, value=round(default_bid, 2), step=1000.0, format="%.2f",
    )

# ==================== RESULT ====================
snapshot = build_history_snapshot(df, row.letting_date, TRAIN_GLOBAL_RATIOS)
project_info = {"engineers_estimate": row.engineers_estimate, "district": row.district}
result = infer(selected_cid, selected_fein, bid_amount, project_info, cand_field, snapshot)

st.divider()

if result["status"] != "ok":
    st.error(f"No probability available — {result.get('reason', result['status'])}")
    st.stop()

st.markdown(
    f"<div style='text-align:center;padding:0.8em 0;'>"
    f"<div style='font-size:1.05em;color:gray;'>Estimated probability of winning</div>"
    f"<div style='font-size:3.4em;font-weight:700;line-height:1.1;'>{result['p_win']*100:.1f}%</div>"
    f"</div>", unsafe_allow_html=True
)

n_valid = result["n_valid_candidates"]
r1, r2, r3 = st.columns(3)
r1.metric("Bid / EE", f"{result['bid_ratio']*100:.1f}%")
r2.metric("Competition", competition_label(n_valid, result["competition_level"]))
r3.metric("Data quality", result["data_quality"])

this_ratio = result["bid_ratio"]
if not (SUPPORTED_BAND[0] <= this_ratio <= SUPPORTED_BAND[1]):
    st.caption("⚠️ This bid is outside the range most represented in the historical data. "
               "The estimate may be less reliable.")
if result.get("unusual_project"):
    st.caption("ℹ️ This project's size is unusual for its district — the estimate may be less reliable.")
if result.get("data_entry_warning"):
    st.caption("⚠️ This bid amount is far from the Engineer's Estimate — double-check the entered value.")

st.caption(
    "Based on historical bidding patterns and the published pre-bid competition field. "
    "**This is an estimate, not a guarantee.**"
)

# ==================== BELOW: BID CURVE ====================
st.divider()
st.subheader("Bid Probability Curve")

DEFAULT_GRID = [0.75, 0.80, 0.85, 0.90, 0.95, 1.00, 1.05, 1.10]
grid = list(DEFAULT_GRID)
# if the entered bid falls outside the default grid, extend the grid to include it,
# clamped to the training-supported (p1-p99) range so we never invent an unsupported ratio
if this_ratio < grid[0] or this_ratio > grid[-1]:
    extended = sorted(set(grid + [round(this_ratio, 4)]))
    grid = [r for r in extended if FULL_BAND[0] <= r <= FULL_BAND[1]]
    if round(this_ratio, 4) not in grid and FULL_BAND[0] <= this_ratio <= FULL_BAND[1]:
        grid = sorted(grid + [round(this_ratio, 4)])

curve_rows = []
for r in grid:
    if r <= 0:
        continue
    res_r = infer(selected_cid, selected_fein, r * row.engineers_estimate, project_info, cand_field, snapshot)
    if res_r["status"] != "ok":
        continue
    curve_rows.append({
        "ratio": r,
        "Bid amount": f"${r * row.engineers_estimate:,.0f}",
        "Bid / EE": f"{r*100:.1f}%",
        "P(win)": f"{res_r['p_win']*100:.1f}%",
        "p_win_raw": res_r["p_win"],
        "your bid": abs(r - round(this_ratio, 4)) < 1e-6,
    })
curve_df = pd.DataFrame(curve_rows)

def highlight_row(row_):
    return ['background-color: #fff3cd'] * len(row_) if row_["your bid"] else [''] * len(row_)

styled = curve_df[["Bid amount", "Bid / EE", "P(win)", "your bid"]].style.apply(highlight_row, axis=1)
st.dataframe(
    styled.hide(axis="columns", subset=["your bid"]),
    hide_index=True, use_container_width=True,
)
st.line_chart(curve_df.set_index("ratio")["p_win_raw"])

mono_ok = all(curve_df["p_win_raw"].values[i] >= curve_df["p_win_raw"].values[i + 1] - 1e-9
              for i in range(len(curve_df) - 1))
st.caption(f"{'✅' if mono_ok else '⚠️'} Curve monotonic (higher bid never increases win probability)")

st.divider()
st.caption(f"Model version: `{result['model_version']}`  |  Demo data as-of: {row.letting_date.date()}")
