"""
QuoteConvert -- V1 Product

"Live INDOT competition intelligence with a validated bid win-probability engine."

Two product modes, plus a regression-test tab:

  MODE A -- Live Competition Intelligence: reads an automatically-discovered
  upcoming INDOT letting's pre-bid candidate field and competition level from a
  pre-generated SNAPSHOT FILE (see below). Works WITHOUT any reference estimate.

  MODE B -- Win-Probability Analysis: the user supplies a Contractor, a Reference
  Estimate, and a Candidate Bid for one of the live projects surfaced in Mode A.
  The bid ratio is passed, unmodified, into the FROZEN inference engine.

  HISTORICAL DEMO (regression test) -- the original as-of historical demo, kept
  only to verify the frozen engine still behaves identically; not part of the V1
  product surface.

DECOUPLED ARCHITECTURE: this Streamlit app does NOT call live_connector's
network functions (discover_upcoming_lettings / ingest_earliest_planholder_list)
directly -- Streamlit's own outbound access to www.in.gov is not reliable. A
separate scheduled process (generate_snapshot.py, run by
.github/workflows/indot_live_refresh.yml, which DOES have reliable INDOT
access) produces two files this app reads from disk:

  data/live_snapshot.json           -- public, no FEIN/PII, rendered directly
  data/live_snapshot_internal.json  -- adds FEIN + district, used ONLY to call
                                        the frozen inference engine in Mode B,
                                        never rendered to the UI

This means the app keeps working even when www.in.gov is completely
unreachable from wherever Streamlit happens to run -- it only ever touches
local disk at request time. It never silently falls back to historical data
if the snapshot is missing, malformed, or stale; it says so explicitly.

This file calls inference.py and live_ui_logic.py; it does not duplicate or
modify either. INDOT does not publish its Engineer's Estimate before bidding
(verified, see DOCS.md) -- so Mode B always asks the user for a Reference
Estimate rather than pretending to obtain INDOT's own EE automatically.
"""
import json
import os
import streamlit as st
import pandas as pd
import numpy as np

import inference
from inference import build_history_snapshot, infer, MODEL_ARTIFACT, competition_level
import live_ui_logic as ui

DATA_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "indot_dataset.csv")
PUBLIC_SNAPSHOT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "live_snapshot.json")
INTERNAL_SNAPSHOT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "live_snapshot_internal.json")

st.set_page_config(page_title="QuoteConvert", page_icon="📐", layout="centered")


@st.cache_data
def load_dataset():
    df = pd.read_csv(DATA_PATH)
    df["letting_date"] = pd.to_datetime(df["letting_date"])
    train = df[df.split == "train"]
    global_ratios = sorted(train.ratio.values)
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


def competition_label(n_valid, level):
    return f"{level} competition — {n_valid} pre-bid candidate{'s' if n_valid != 1 else ''}"


@st.cache_data(ttl=30)
def load_snapshot_file(path):
    """Reads a snapshot JSON file from local disk only -- no network call.
    Returns (snapshot_dict_or_None, error_message_or_None). A missing or
    unparseable file is reported explicitly, never silently treated as
    'no upcoming letting'."""
    if not os.path.exists(path):
        return None, "snapshot file not found"
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f), None
    except (json.JSONDecodeError, OSError) as e:
        return None, f"snapshot file could not be read/parsed: {e}"


def load_live_state():
    """Loads the PUBLIC snapshot from disk and classifies it into exactly one
    of the 5 app-level states (live_ui_logic.classify_app_state). Never calls
    INDOT directly -- see the module docstring for why."""
    public, read_error = load_snapshot_file(PUBLIC_SNAPSHOT_PATH)
    app_state = ui.classify_app_state(public)

    result = {"state": app_state, "snapshot": public, "read_error": read_error}
    if public is not None and app_state in (ui.APP_STATE_LIVE_CANDIDATES_AVAILABLE, ui.APP_STATE_STALE_SNAPSHOT):
        letting = public["letting"]
        result.update({
            "letting_date": letting["letting_date"],
            "letting_page_url": letting["letting_page_url"],
            "planholder_url": letting.get("planholder_url"),
            "planholder_retrieved_at": letting.get("planholder_retrieved_at"),
            "retrieved_at": public["retrieved_at"],
            "contracts": letting["contracts"],
        })
    elif public is not None and app_state == ui.APP_STATE_NO_PREBID_CANDIDATE_LIST:
        result.update({
            "letting_date": public["letting"].get("letting_date"),
            "letting_page_url": public["letting"].get("letting_page_url"),
        })
    return result


def load_internal_directory():
    """Reads the INTERNAL snapshot (FEIN + district included) -- used only to
    supply candidate FEINs to the frozen inference engine in Mode B. Never
    rendered to the UI directly. Returns {contract_id: {...}} or {} if
    unavailable, mirroring the public snapshot's availability."""
    internal, _err = load_snapshot_file(INTERNAL_SNAPSHOT_PATH)
    if internal is None or internal["letting"]["status"] != ui.SNAPSHOT_STATUS_AVAILABLE:
        return {}
    return {c["contract_id"]: c for c in internal["letting"]["contracts"]}


# ------------------------------------------------------------------
st.title("QuoteConvert")
st.caption("Live INDOT competition intelligence with a validated bid win-probability engine.")

tab_a, tab_b, tab_demo = st.tabs([
    "Live Competition", "Win-Probability Analysis", "Historical Demo (regression test)",
])

live = load_live_state()

# ==================== MODE A -- LIVE COMPETITION INTELLIGENCE ====================
with tab_a:
    st.subheader("Upcoming INDOT Letting")
    st.caption("Sourced from a snapshot refreshed on a schedule by GitHub Actions — "
               "this page never queries INDOT directly.")

    if live["state"] == ui.APP_STATE_REFRESH_FAILED:
        st.error(
            "No valid live snapshot is available "
            f"({live['read_error'] or 'snapshot failed schema validation'}). "
            "This is not substituted with historical data — check that the "
            "indot_live_refresh workflow has run successfully at least once."
        )
    elif live["state"] == ui.APP_STATE_NO_UPCOMING_LETTING:
        st.info("No upcoming INDOT letting was found as of the last snapshot refresh. Check again later.")
    elif live["state"] == ui.APP_STATE_NO_PREBID_CANDIDATE_LIST:
        st.metric("Letting date", live.get("letting_date", "unknown"))
        st.info("Competition analysis will be available once INDOT publishes the Planholder List.")
    else:  # APP_STATE_LIVE_CANDIDATES_AVAILABLE or APP_STATE_STALE_SNAPSHOT
        if live["state"] == ui.APP_STATE_STALE_SNAPSHOT:
            st.warning(f"⚠️ Live data may be stale — last refreshed {live['retrieved_at']}.")
        st.metric("Letting date", live["letting_date"])
        st.caption(
            f"Source: [{live['letting_page_url']}]({live['letting_page_url']})  |  "
            f"Planholder List retrieved {live['planholder_retrieved_at']}  |  "
            f"Snapshot refreshed {live['retrieved_at']}"
        )
        contracts_live = live["contracts"]
        if not contracts_live:
            st.warning("Planholder List published, but no contracts could be parsed from it.")
        else:
            table = []
            for c in contracts_live:
                n_valid = len(c["valid_for_bid"])
                table.append({
                    "Contract": c["contract_id"],
                    "Pre-bid candidates": n_valid,
                    "Competition": competition_level(n_valid) if n_valid > 0 else "—",
                })
            st.dataframe(pd.DataFrame(table), hide_index=True, use_container_width=True)
            st.caption(f"{len(contracts_live)} contract(s) in this letting. Select a project in "
                       "**Win-Probability Analysis** to run a prediction.")

# ==================== MODE B -- WIN-PROBABILITY ANALYSIS ====================
with tab_b:
    st.subheader("Win-Probability Analysis")

    if live["state"] not in (ui.APP_STATE_LIVE_CANDIDATES_AVAILABLE, ui.APP_STATE_STALE_SNAPSHOT):
        st.info(
            "Win-probability analysis requires a live, published Planholder List for an "
            "upcoming letting. See the **Live Competition** tab for current status."
        )
    else:
        internal_directory = load_internal_directory()
        cid_options = sorted(internal_directory.keys())
        if not cid_options:
            st.error("The live snapshot has no parsed contracts to run a prediction against.")
        else:
            if live["state"] == ui.APP_STATE_STALE_SNAPSHOT:
                st.warning(f"⚠️ Live data may be stale — last refreshed {live['retrieved_at']}.")
            selected_cid = st.selectbox("Project (upcoming INDOT contract)", cid_options, key="modeb_cid")
            info = internal_directory[selected_cid]
            candidates = info["all_candidates"]
            n_valid = sum(1 for c in candidates if c.get("valid_for_bid") == "Yes")

            cand_names = {c["fein"]: f"{c.get('name', c['fein'])}" + ("" if c["valid_for_bid"] == "Yes" else " (not Valid For Bid)")
                          for c in candidates if c.get("fein")}
            if not cand_names:
                st.error("No contractors with a FEIN could be parsed for this contract.")
            else:
                b1, b2 = st.columns(2)
                with b1:
                    selected_fein = st.selectbox(
                        "Contractor", list(cand_names.keys()), format_func=lambda f: cand_names[f], key="modeb_fein",
                    )
                with b2:
                    st.metric("Competition", competition_label(n_valid, competition_level(n_valid) if n_valid > 0 else "—"))

            st.markdown("**Reference Estimate ($)**")
            st.caption(
                "Enter the estimate you are using for this project. QuoteConvert's historical "
                "model was trained using INDOT Engineer's Estimates; using a different reference "
                "estimate may affect calibration."
            )
            reference_estimate = st.number_input(
                "Reference Estimate ($)", min_value=0.0, value=0.0, step=1000.0, format="%.2f",
                key="modeb_ref", label_visibility="collapsed",
            )
            bid_amount = st.number_input(
                "Candidate Bid ($)", min_value=0.0, value=0.0, step=1000.0, format="%.2f", key="modeb_bid",
            )

            cstatus = ui.contractor_status(candidates, selected_fein)
            pred_state = ui.determine_prediction_state(reference_estimate or None, bid_amount or None)

            st.divider()

            if cstatus != "valid":
                st.error(
                    "The selected contractor is not currently listed as Valid For Bid on this "
                    "project's published Planholder List — a win-probability estimate cannot be produced."
                )
            elif pred_state == ui.STATE_REFERENCE_ESTIMATE_REQUIRED:
                st.warning(
                    "Enter a Reference Estimate to enable the win-probability panel. Competition "
                    "information above remains available without it."
                )
            elif pred_state == ui.STATE_BID_AMOUNT_REQUIRED:
                st.warning("Enter a Candidate Bid to run a win-probability prediction.")
            else:
                candidate_field = candidates
                project_info = {"engineers_estimate": reference_estimate, "district": info["district"]}
                # live "as-of" history: everything on record strictly before today
                as_of = pd.Timestamp.now(tz="UTC").tz_localize(None)
                snapshot = build_history_snapshot(df, as_of, TRAIN_GLOBAL_RATIOS)
                result = infer(selected_cid, selected_fein, bid_amount, project_info, candidate_field, snapshot)

                if result["status"] != "ok":
                    st.error(f"No probability available — {result.get('reason', result['status'])}")
                else:
                    st.markdown(
                        f"<div style='text-align:center;padding:0.8em 0;'>"
                        f"<div style='font-size:1.05em;color:gray;'>Estimated probability of winning</div>"
                        f"<div style='font-size:3.4em;font-weight:700;line-height:1.1;'>{result['p_win']*100:.1f}%</div>"
                        f"</div>", unsafe_allow_html=True,
                    )
                    r1, r2, r3 = st.columns(3)
                    r1.metric("Bid / Reference Estimate", f"{result['bid_ratio']*100:.1f}%")
                    r2.metric("Competition", result["competition_level"])
                    r3.metric("Data quality", result["data_quality"])

                    this_ratio = result["bid_ratio"]
                    if not (SUPPORTED_BAND[0] <= this_ratio <= SUPPORTED_BAND[1]):
                        st.caption("⚠️ This bid is outside the range most represented in the historical data. "
                                   "The estimate may be less reliable.")
                    if result.get("unusual_project"):
                        st.caption("ℹ️ This project's size is unusual for its district — the estimate may be less reliable.")
                    if result.get("data_entry_warning"):
                        st.caption("⚠️ This bid amount is far from the Reference Estimate — double-check the entered value.")
                    st.caption(
                        "Based on historical bidding patterns and the current published pre-bid "
                        "competition field. **This is an estimate, not a guarantee.**"
                    )

                    st.divider()
                    st.subheader("Bid Probability Curve")
                    DEFAULT_GRID = [0.75, 0.80, 0.85, 0.90, 0.95, 1.00, 1.05, 1.10]
                    grid = list(DEFAULT_GRID)
                    if this_ratio < grid[0] or this_ratio > grid[-1]:
                        extended = sorted(set(grid + [round(this_ratio, 4)]))
                        grid = [r for r in extended if FULL_BAND[0] <= r <= FULL_BAND[1]]

                    curve_rows = []
                    for r in grid:
                        if r <= 0:
                            continue
                        res_r = infer(selected_cid, selected_fein, r * reference_estimate, project_info, candidate_field, snapshot)
                        if res_r["status"] != "ok":
                            continue
                        curve_rows.append({
                            "ratio": r,
                            "Bid amount": f"${r * reference_estimate:,.0f}",
                            "Bid / Reference Estimate": f"{r*100:.1f}%",
                            "P(win)": f"{res_r['p_win']*100:.1f}%",
                            "p_win_raw": res_r["p_win"],
                            "your bid": abs(r - round(this_ratio, 4)) < 1e-6,
                        })
                    curve_df = pd.DataFrame(curve_rows)

                    def highlight_row(row_):
                        return ['background-color: #fff3cd'] * len(row_) if row_["your bid"] else [''] * len(row_)

                    styled = curve_df[["Bid amount", "Bid / Reference Estimate", "P(win)", "your bid"]].style.apply(highlight_row, axis=1)
                    st.dataframe(styled.hide(axis="columns", subset=["your bid"]), hide_index=True, use_container_width=True)
                    st.line_chart(curve_df.set_index("ratio")["p_win_raw"])

                    mono_ok = all(curve_df["p_win_raw"].values[i] >= curve_df["p_win_raw"].values[i + 1] - 1e-9
                                  for i in range(len(curve_df) - 1))
                    st.caption(f"{'✅' if mono_ok else '⚠️'} Curve monotonic (higher bid never increases win probability)")
                    st.caption(f"Model version: `{result['model_version']}`")

# ==================== HISTORICAL DEMO (regression test) ====================
with tab_demo:
    st.warning(
        "**Regression-test tab — not part of the V1 product.** Reconstructed strictly as it "
        "would have appeared *before* each historical contract's bid deadline, using that "
        "contract's real Engineer's Estimate. Kept only to verify the frozen engine still "
        "produces identical output; use **Win-Probability Analysis** for live projects."
    )

    default_cid = "B-40989-A" if "B-40989-A" in contracts.contract_id.values else contracts.contract_id.iloc[0]
    options = contracts.contract_id.tolist()
    default_pos = options.index(default_cid) if default_cid in options else 0

    selected_cid_h = st.selectbox(
        "Historical INDOT contract", options, index=default_pos,
        format_func=lambda cid: f"{cid} — {contracts.loc[contracts.contract_id==cid,'letting_date'].dt.date.values[0]}",
        key="demo_cid",
    )

    row = contracts[contracts.contract_id == selected_cid_h].iloc[0]
    sub = df[df.contract_id == selected_cid_h]
    cand_field_h = json.loads(sub.candidates_json.iloc[0])
    valid_candidates_h = [c for c in cand_field_h if c.get("valid_for_bid") == "Yes" and c.get("fein")]

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Letting date", str(row.letting_date.date()))
    c2.metric("District", row.district)
    c3.metric("Engineer's Estimate", f"${row.engineers_estimate:,.0f}")
    c4.metric("Pre-bid candidates", int(row.n_candidates))
    st.caption(f"County: {row.county}  |  Contract ID: {selected_cid_h}")

    if not valid_candidates_h:
        st.error("No pre-bid candidate list is available for this contract.")
    else:
        cand_names_h = {c["fein"]: c.get("name", c["fein"]) for c in valid_candidates_h}
        hc1, hc2 = st.columns([1, 1])
        with hc1:
            selected_fein_h = st.selectbox(
                "Contractor", list(cand_names_h.keys()), format_func=lambda f: cand_names_h[f], key="demo_fein",
            )
        with hc2:
            bid_amount_h = st.number_input(
                "Hypothetical bid amount ($)", min_value=0.0, value=round(float(row.engineers_estimate), 2),
                step=1000.0, format="%.2f", key="demo_bid",
            )

        snapshot_h = build_history_snapshot(df, row.letting_date, TRAIN_GLOBAL_RATIOS)
        project_info_h = {"engineers_estimate": row.engineers_estimate, "district": row.district}
        result_h = infer(selected_cid_h, selected_fein_h, bid_amount_h, project_info_h, cand_field_h, snapshot_h)

        if result_h["status"] != "ok":
            st.error(f"No probability available — {result_h.get('reason', result_h['status'])}")
        else:
            st.metric("Estimated probability of winning", f"{result_h['p_win']*100:.1f}%")
            st.caption(f"Bid/EE: {result_h['bid_ratio']*100:.1f}%  |  Competition: {result_h['competition_level']}  |  "
                       f"Data quality: {result_h['data_quality']}  |  Model version: `{result_h['model_version']}`")
