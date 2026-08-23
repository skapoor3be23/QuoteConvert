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
            "valid_for_bid_count": letting.get("valid_for_bid_count"),
        })
    elif public is not None and app_state == ui.APP_STATE_NO_PREBID_CANDIDATE_LIST:
        result.update({
            "letting_date": public["letting"].get("letting_date"),
            "letting_page_url": public["letting"].get("letting_page_url"),
            "retrieved_at": public["retrieved_at"],
        })
    return result


def _format_date_human(iso_date):
    """Presentation only -- 'YYYY-MM-DD' -> 'September 2, 2026'. Never invents
    a date; returns the original string unchanged if it can't be parsed."""
    if not iso_date:
        return "Unknown"
    try:
        import datetime as _dt
        d = _dt.datetime.strptime(iso_date, "%Y-%m-%d")
        return f"{d.strftime('%B')} {d.day}, {d.year}"
    except ValueError:
        return iso_date


def _format_timestamp_human(iso_ts):
    """Presentation only -- renders an ISO timestamp already present in the
    snapshot as 'YYYY-MM-DD HH:MM UTC'. Never invents a timestamp; returns the
    raw string unchanged if it can't be parsed."""
    if not iso_ts:
        return "Unknown"
    try:
        import datetime as _dt
        dt = _dt.datetime.fromisoformat(iso_ts.replace("Z", "+00:00"))
        return dt.strftime("%Y-%m-%d %H:%M UTC")
    except (ValueError, AttributeError):
        return iso_ts


def _render_status_header(letting_date_display, snapshot_status_label, retrieved_at_display):
    """Compact 4-up header card used across every Live Competition state."""
    with st.container(border=True):
        h1, h2, h3, h4 = st.columns(4)
        h1.metric("Letting Date", letting_date_display)
        h2.metric("Data Source", "INDOT")
        h3.metric("Snapshot Status", snapshot_status_label)
        h4.metric("Last Refresh", retrieved_at_display)


def _render_data_integrity_note():
    st.caption(
        "QuoteConvert never substitutes historical bidder data for missing live pre-bid data."
    )


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
    st.markdown("### QuoteConvert / Live Competition")
    st.caption("Live pre-bid competition intelligence sourced from INDOT.")

    if live["state"] == ui.APP_STATE_REFRESH_FAILED:
        _render_status_header("Unknown", "Refresh Failed", "Unknown")
        with st.container(border=True):
            st.markdown("**No valid live snapshot is available**")
            st.write(live["read_error"] or "The snapshot failed schema validation.")
            st.caption(
                "This is not substituted with historical data — check that the "
                "`indot_live_refresh` GitHub Actions workflow has run successfully at least once."
            )
        _render_data_integrity_note()

    elif live["state"] == ui.APP_STATE_NO_UPCOMING_LETTING:
        _render_status_header("Unknown", "No Upcoming Letting", _format_timestamp_human(live.get("retrieved_at")))
        with st.container(border=True):
            st.markdown("**No upcoming INDOT letting found**")
            st.write("As of the last snapshot refresh, INDOT had no upcoming letting listed.")
            st.caption("QuoteConvert will automatically pick up the next upcoming letting during "
                       "the next scheduled GitHub Actions refresh.")
        _render_data_integrity_note()

    elif live["state"] == ui.APP_STATE_NO_PREBID_CANDIDATE_LIST:
        _render_status_header(
            _format_date_human(live.get("letting_date")),
            "Awaiting Planholder List",
            _format_timestamp_human(live.get("retrieved_at")),
        )
        with st.container(border=True):
            st.markdown("**Planholder List not published yet**")
            st.write(
                "INDOT has published the upcoming letting, but the current pre-bid "
                "Planholder List is not available yet."
            )
            st.caption(
                "QuoteConvert will automatically pick up the Planholder List during the "
                "next scheduled GitHub Actions refresh."
            )
            st.markdown("**Next action:** Come back after the next refresh.")
        _render_data_integrity_note()

    else:  # APP_STATE_LIVE_CANDIDATES_AVAILABLE or APP_STATE_STALE_SNAPSHOT
        is_stale = live["state"] == ui.APP_STATE_STALE_SNAPSHOT
        _render_status_header(
            _format_date_human(live["letting_date"]),
            "Stale" if is_stale else "Live snapshot",
            _format_timestamp_human(live["retrieved_at"]),
        )
        if is_stale:
            st.warning(f"Live data may be stale — last refreshed {_format_timestamp_human(live['retrieved_at'])}.")

        contracts_live = live["contracts"]
        if not contracts_live:
            st.warning("Planholder List published, but no contracts could be parsed from it.")
        else:
            m1, m2, m3 = st.columns(3)
            m1.metric("Available contracts", len(contracts_live))
            m2.metric("Total Valid-For-Bid candidates", live.get("valid_for_bid_count", "—"))
            m3.metric("Planholder retrieved", _format_timestamp_human(live.get("planholder_retrieved_at")))

            st.divider()
            cid_options_a = [c["contract_id"] for c in contracts_live]
            selected_cid_a = st.selectbox("Contract", cid_options_a, key="modea_cid")
            sel = next(c for c in contracts_live if c["contract_id"] == selected_cid_a)
            n_valid_a = len(sel["valid_for_bid"])

            d1, d2 = st.columns(2)
            d1.metric("Pre-bid candidates", n_valid_a)
            d2.metric("Competition", competition_level(n_valid_a) if n_valid_a > 0 else "—")
            if sel["valid_for_bid"]:
                st.caption("Valid-For-Bid contractors:")
                st.dataframe(
                    pd.DataFrame({"Contractor": [c["contractor_name"] for c in sel["valid_for_bid"]]}),
                    hide_index=True, use_container_width=True,
                )
            st.caption("Select a project in **Win-Probability Analysis** to run a prediction.")
        _render_data_integrity_note()

def _win_interpretation(p_win):
    """Presentation only -- a plain-language label for the same p_win the
    frozen engine already returned. Never implies certainty."""
    if p_win >= 0.60:
        return "QuoteConvert estimates a relatively strong win likelihood at this bid level."
    if p_win >= 0.30:
        return "QuoteConvert estimates a moderate win likelihood at this bid level."
    return "QuoteConvert estimates a relatively low win likelihood at this bid level."


# ==================== MODE B -- WIN-PROBABILITY ANALYSIS ====================
with tab_b:
    st.markdown("### Win-Probability Analysis")
    st.caption("Estimate your bid's probability of winning using QuoteConvert's validated historical model.")

    if live["state"] == ui.APP_STATE_NO_PREBID_CANDIDATE_LIST:
        with st.container(border=True):
            st.markdown("**Live prediction is waiting for the current Planholder List.**")
            st.write(
                "Competition and contractor selection will become available after the next "
                "successful live refresh detects the published Planholder List."
            )
        _render_data_integrity_note()
    elif live["state"] not in (ui.APP_STATE_LIVE_CANDIDATES_AVAILABLE, ui.APP_STATE_STALE_SNAPSHOT):
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
                st.warning(f"Live data may be stale — last refreshed {_format_timestamp_human(live['retrieved_at'])}.")

            selected_cid = st.selectbox("Project (upcoming INDOT contract)", cid_options, key="modeb_cid")
            info = internal_directory[selected_cid]
            candidates = info["all_candidates"]
            n_valid = sum(1 for c in candidates if c.get("valid_for_bid") == "Yes")

            # ---- Live project context (values already in the snapshot only) ----
            with st.container(border=True):
                p1, p2, p3 = st.columns(3)
                p1.metric("Letting Date", _format_date_human(live["letting_date"]))
                p2.metric("Project / Contract", selected_cid)
                p3.metric("Valid-For-Bid Candidates", n_valid)
                p4, p5 = st.columns(2)
                p4.metric("Competition Level", competition_level(n_valid) if n_valid > 0 else "—")
                p5.metric("Snapshot Last Refreshed", _format_timestamp_human(live["retrieved_at"]))

            cand_names = {c["fein"]: c.get("name", c["fein"])
                          for c in candidates if c.get("fein") and c.get("valid_for_bid") == "Yes"}

            # ---- Input area ----
            with st.container(border=True):
                st.markdown("**Your Bid Scenario**")
                if not cand_names:
                    st.error("No Valid-For-Bid contractors with a FEIN could be parsed for this contract.")
                    selected_fein = None
                else:
                    selected_fein = st.selectbox(
                        "Contractor", list(cand_names.keys()), format_func=lambda f: cand_names[f], key="modeb_fein",
                    )

                reference_estimate = st.number_input(
                    "Reference Estimate ($)", min_value=0.0, value=0.0, step=1000.0, format="%.2f",
                    key="modeb_ref",
                    help=("Enter the estimate you are using as the project's reference value. "
                          "QuoteConvert's historical model was trained using INDOT Engineer's "
                          "Estimates; a different reference estimate may affect calibration."),
                )
                bid_amount = st.number_input(
                    "Candidate Bid ($)", min_value=0.0, value=0.0, step=1000.0, format="%.2f", key="modeb_bid",
                    help="Enter the bid amount you are considering.",
                )

            cstatus = ui.contractor_status(candidates, selected_fein) if selected_fein else "not_found"
            pred_state = ui.determine_prediction_state(reference_estimate or None, bid_amount or None)

            if cstatus != "valid":
                st.error(
                    "The selected contractor is not currently listed as Valid For Bid on this "
                    "project's published Planholder List — a win-probability estimate cannot be produced."
                )
            elif pred_state == ui.STATE_REFERENCE_ESTIMATE_REQUIRED:
                st.warning(
                    "Enter a Reference Estimate greater than zero to enable the win-probability panel."
                )
            elif pred_state == ui.STATE_BID_AMOUNT_REQUIRED:
                st.warning("Enter a Candidate Bid greater than zero to run a win-probability prediction.")
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
                    with st.container(border=True):
                        st.markdown(
                            f"<div style='text-align:center;padding:0.8em 0;'>"
                            f"<div style='font-size:1.05em;color:gray;'>Estimated Probability of Winning</div>"
                            f"<div style='font-size:3.4em;font-weight:700;line-height:1.1;'>{result['p_win']*100:.1f}%</div>"
                            f"</div>", unsafe_allow_html=True,
                        )
                        st.caption(_win_interpretation(result["p_win"]))
                        r1, r2, r3 = st.columns(3)
                        r1.metric("Bid / Reference Estimate", f"{result['bid_ratio']*100:.1f}%")
                        r2.metric("Competition", result["competition_level"])
                        r3.metric("Data Quality", result["data_quality"])

                    this_ratio = result["bid_ratio"]
                    if not (SUPPORTED_BAND[0] <= this_ratio <= SUPPORTED_BAND[1]):
                        st.caption("This bid is outside the range most represented in the historical data. "
                                   "The estimate may be less reliable.")
                    if result.get("unusual_project"):
                        st.caption("This project's size is unusual for its district — the estimate may be less reliable.")
                    if result.get("data_entry_warning"):
                        st.caption("This bid amount is far from the Reference Estimate — double-check the entered value.")
                    st.caption("**This is an estimate, not a guarantee.**")
                    st.caption(
                        "Prediction uses the current live pre-bid contractor field and the reference "
                        "estimate you provide. QuoteConvert does not use post-bid results for live prediction."
                    )

                    st.divider()
                    st.markdown("**How Bid Level Changes Estimated Win Probability**")
                    st.caption("Lower bid ratios generally correspond to higher estimated win "
                               "probability in the validated model.")
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
                    st.caption(f"{'Monotonic' if mono_ok else 'Not monotonic'} — higher bid never increases win probability" if mono_ok
                               else "⚠️ Curve monotonicity check failed")
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
