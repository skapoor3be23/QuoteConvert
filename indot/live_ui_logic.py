"""
Pure, Streamlit-free logic for the V1 product UI.

This module contains ONLY: (1) the live-data state machine used by Mode A/Mode B,
(2) input validation, and (3) building a per-contract candidate directory from an
already-downloaded Planholder PDF. It has no Streamlit dependency, so it can be
unit-tested directly (see tests/test_live_ui_logic.py) without running the app or
touching the network.

It imports (never modifies) parse_indot.py and inference.py. The PLAN_NAME_RE /
FEIN_RE / DISTRICT_RE parsing pattern used in build_contract_directory() is the
exact same pattern live_connector.run_live_pipeline() already uses inline for a
single contract -- this module just generalizes it to every contract in a
Planholder PDF, without duplicating any regex.
"""
from datetime import datetime, timezone

import parse_indot as pi
from inference import competition_level

STATE_NO_UPCOMING_LETTING = "no_upcoming_letting"
STATE_NO_PREBID_CANDIDATE_LIST = "no_prebid_candidate_list"
STATE_LIVE_CANDIDATES_AVAILABLE = "live_candidates_available"
STATE_REFERENCE_ESTIMATE_REQUIRED = "reference_estimate_required"
STATE_PREDICTION_READY = "prediction_ready"

# not one of the 5 canonical states, but a distinct sub-state of "not yet ready"
# so a missing bid is never conflated with a missing reference estimate
STATE_BID_AMOUNT_REQUIRED = "bid_amount_required"

# ------------------------------------------------------------------
# Snapshot-based app state machine (decoupled deployment architecture).
#
# Streamlit no longer calls live_connector's network functions directly (see
# app.py) -- it reads a snapshot file generated on a schedule by GitHub
# Actions (the only component with real INDOT outbound access) and classifies
# it into exactly one of 5 app-level states, never silently downgrading to
# historical data as a substitute for a missing/stale live snapshot.
# ------------------------------------------------------------------

SNAPSHOT_STATUS_AVAILABLE = "available"
SNAPSHOT_STATUS_NO_PREBID = "no_prebid_candidate_list"
SNAPSHOT_STATUS_NO_UPCOMING = "no_upcoming_letting"
_VALID_SNAPSHOT_STATUSES = {SNAPSHOT_STATUS_AVAILABLE, SNAPSHOT_STATUS_NO_PREBID, SNAPSHOT_STATUS_NO_UPCOMING}

APP_STATE_LIVE_CANDIDATES_AVAILABLE = "live_candidates_available"
APP_STATE_NO_PREBID_CANDIDATE_LIST = "no_prebid_candidate_list"
APP_STATE_NO_UPCOMING_LETTING = "no_upcoming_letting"
APP_STATE_REFRESH_FAILED = "refresh_failed"
APP_STATE_STALE_SNAPSHOT = "stale_snapshot"

DEFAULT_FRESHNESS_THRESHOLD_HOURS = 24

# keys that must never appear anywhere in the PUBLIC (user-facing) snapshot --
# FEIN is a real business identifier and district/candidate detail beyond a
# name is fine, but this is the hard privacy boundary for the public file.
PUBLIC_FORBIDDEN_KEYS = {"fein", "phone", "email", "address", "ssn"}


def _scan_for_forbidden_keys(obj, forbidden):
    """Recursively scans a JSON-like structure for any dict key (case-
    insensitive) in `forbidden`. Returns the offending key, or None."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(k, str) and k.lower() in forbidden:
                return k
            found = _scan_for_forbidden_keys(v, forbidden)
            if found:
                return found
    elif isinstance(obj, list):
        for item in obj:
            found = _scan_for_forbidden_keys(item, forbidden)
            if found:
                return found
    return None


def build_snapshot(retrieved_at, source_archive_url, letting_date, letting_page_url, status,
                    planholder_url=None, planholder_retrieved_at=None, planholder_sha256=None,
                    candidate_count=None, valid_for_bid_count=None, directory=None, include_fein=False):
    """Builds either the PUBLIC snapshot (include_fein=False, matches the
    committed indot/data/live_snapshot.json schema exactly -- no FEIN, no PII)
    or the INTERNAL snapshot (include_fein=True, adds FEIN + district per
    contract so app.py's Win-Probability mode can still call the frozen
    inference engine, which requires FEIN to key contractor history).

    `directory`: the dict returned by build_contract_directory() -- only
    required/used when status == SNAPSHOT_STATUS_AVAILABLE.
    """
    letting = {
        "letting_date": letting_date,
        "letting_page_url": letting_page_url,
        "status": status,
    }
    if status == SNAPSHOT_STATUS_AVAILABLE:
        letting.update({
            "planholder_url": planholder_url,
            "planholder_retrieved_at": planholder_retrieved_at,
            "planholder_sha256": planholder_sha256,
            "candidate_count": candidate_count,
            "valid_for_bid_count": valid_for_bid_count,
            "contracts": [],
        })
        for cid, info in sorted((directory or {}).items()):
            valid_list = [c for c in info["candidates"] if c.get("valid_for_bid") == "Yes"]
            if include_fein:
                valid_for_bid_entries = [{"contractor_name": c["name"], "fein": c["fein"]} for c in valid_list]
            else:
                valid_for_bid_entries = [{"contractor_name": c["name"]} for c in valid_list]
            contract_entry = {
                "contract_id": cid,
                "candidate_count": len(info["candidates"]),
                "valid_for_bid": valid_for_bid_entries,
            }
            if include_fein:
                contract_entry["district"] = info.get("district")
                # full candidate list (including "No" entries) -- needed so
                # contractor_status() can distinguish not_found vs not_valid_for_bid
                contract_entry["all_candidates"] = info["candidates"]
            letting["contracts"].append(contract_entry)
    return {
        "snapshot_version": 1,
        "retrieved_at": retrieved_at,
        "source": "INDOT",
        "source_archive_url": source_archive_url,
        "letting": letting,
    }


def validate_public_snapshot_schema(snapshot):
    """Raises ValueError with a specific reason if `snapshot` does not match
    the required public schema, or if it contains any forbidden PII/internal
    key anywhere. Returns True on success."""
    if not isinstance(snapshot, dict):
        raise ValueError("snapshot is not a JSON object")
    for key in ("snapshot_version", "retrieved_at", "source", "letting"):
        if key not in snapshot:
            raise ValueError(f"missing required top-level key: {key}")
    letting = snapshot["letting"]
    if not isinstance(letting, dict) or "status" not in letting:
        raise ValueError("letting.status missing")
    if letting["status"] not in _VALID_SNAPSHOT_STATUSES:
        raise ValueError(f"unrecognized letting.status: {letting['status']!r}")
    if letting["status"] == SNAPSHOT_STATUS_AVAILABLE:
        if "contracts" not in letting or not isinstance(letting["contracts"], list):
            raise ValueError("letting.contracts missing or not a list for status=available")
        for c in letting["contracts"]:
            if "contract_id" not in c or "valid_for_bid" not in c:
                raise ValueError(f"malformed contract entry: {c}")
    forbidden_hit = _scan_for_forbidden_keys(snapshot, PUBLIC_FORBIDDEN_KEYS)
    if forbidden_hit:
        raise ValueError(f"forbidden key found in public snapshot: {forbidden_hit!r}")
    return True


def is_snapshot_stale(retrieved_at_iso, now=None, threshold_hours=DEFAULT_FRESHNESS_THRESHOLD_HOURS):
    """True if `retrieved_at_iso` is more than `threshold_hours` in the past.
    Any unparseable timestamp is treated as stale (fail safe, never silently
    treated as fresh)."""
    try:
        retrieved_at = datetime.fromisoformat(retrieved_at_iso.replace("Z", "+00:00"))
        if retrieved_at.tzinfo is None:
            retrieved_at = retrieved_at.replace(tzinfo=timezone.utc)
    except (ValueError, AttributeError, TypeError):
        return True
    now = now or datetime.now(timezone.utc)
    age_seconds = (now - retrieved_at).total_seconds()
    return age_seconds > threshold_hours * 3600


def classify_app_state(snapshot, now=None, freshness_threshold_hours=DEFAULT_FRESHNESS_THRESHOLD_HOURS):
    """Classifies a loaded (or missing/malformed) snapshot into exactly one of
    the 5 app-level states. Never falls through to a state the data doesn't
    actually support.

    `snapshot`: the parsed public snapshot dict, or None if the file was
    missing/unreadable/failed to parse as JSON.
    """
    if snapshot is None:
        return APP_STATE_REFRESH_FAILED
    try:
        validate_public_snapshot_schema(snapshot)
    except ValueError:
        return APP_STATE_REFRESH_FAILED

    status = snapshot["letting"]["status"]
    if status == SNAPSHOT_STATUS_NO_UPCOMING:
        return APP_STATE_NO_UPCOMING_LETTING
    if status == SNAPSHOT_STATUS_NO_PREBID:
        return APP_STATE_NO_PREBID_CANDIDATE_LIST
    # status == SNAPSHOT_STATUS_AVAILABLE
    if is_snapshot_stale(snapshot.get("retrieved_at"), now=now, threshold_hours=freshness_threshold_hours):
        return APP_STATE_STALE_SNAPSHOT
    return APP_STATE_LIVE_CANDIDATES_AVAILABLE


def determine_discovery_state(discover_results, ingest_status):
    """discover_results: the `results` list from discover_upcoming_lettings()
    (possibly empty). ingest_status: the status string from
    ingest_earliest_planholder_list(), or None if it was never called (because
    discover_results is empty, or the earliest letting has no Planholder link yet).

    Returns one of STATE_NO_UPCOMING_LETTING / STATE_NO_PREBID_CANDIDATE_LIST /
    STATE_LIVE_CANDIDATES_AVAILABLE. Never silently falls through to a different
    state than what the inputs actually support.
    """
    if not discover_results:
        return STATE_NO_UPCOMING_LETTING
    earliest = discover_results[0]
    if not earliest.get("planholder_list_available"):
        return STATE_NO_PREBID_CANDIDATE_LIST
    if ingest_status != "ok":
        return STATE_NO_PREBID_CANDIDATE_LIST
    return STATE_LIVE_CANDIDATES_AVAILABLE


def is_valid_positive_number(x):
    """True only for a value that is present and strictly positive. Blank/None/
    zero/negative/non-numeric all return False -- callers must not silently
    treat any of these as a valid input."""
    if x is None:
        return False
    try:
        return float(x) > 0
    except (TypeError, ValueError):
        return False


def determine_prediction_state(reference_estimate, bid_amount):
    """Given the user's Reference Estimate and Bid Amount inputs (either may be
    None/blank/invalid), returns STATE_REFERENCE_ESTIMATE_REQUIRED,
    STATE_BID_AMOUNT_REQUIRED, or STATE_PREDICTION_READY. Reference estimate is
    checked first, matching the spec's stated precedence ("If reference estimate
    is missing: ... P(win) panel is disabled" is listed before the bid-missing case)."""
    if not is_valid_positive_number(reference_estimate):
        return STATE_REFERENCE_ESTIMATE_REQUIRED
    if not is_valid_positive_number(bid_amount):
        return STATE_BID_AMOUNT_REQUIRED
    return STATE_PREDICTION_READY


def contractor_status(candidate_field, fein):
    """Where a contractor stands relative to a contract's CURRENT candidate field.
    Returns "not_found" (not on the list at all), "not_valid_for_bid" (listed but
    not Valid For Bid), or "valid"."""
    match = next((c for c in candidate_field if c.get("fein") == fein), None)
    if match is None:
        return "not_found"
    if match.get("valid_for_bid") != "Yes":
        return "not_valid_for_bid"
    return "valid"


def build_contract_directory(local_pdf_path):
    """Parse an already-downloaded Planholder PDF into a per-contract directory:
    {contract_id: {"candidates": [...], "n_valid_candidates": int,
                   "competition_level": "Low"/"Moderate"/"High"/None, "district": str or None}}

    Reuses parse_indot.py's validated primitives directly (full_text, load_blocks,
    PLAN_NAME_RE, FEIN_RE, DISTRICT_RE, clean) -- the same parsing pattern
    live_connector.run_live_pipeline() already applies to a single contract, here
    generalized across every contract block in the PDF. Does not fetch anything;
    the PDF must already be on disk (Stage 2 of live_connector.py).
    """
    txt = pi.full_text(local_pdf_path)
    blocks = pi.load_blocks(txt)
    directory = {}
    for cid, segs in blocks.items():
        ptxt = segs[0]
        candidates = []
        for m in pi.PLAN_NAME_RE.finditer(ptxt):
            name = pi.clean(m.group(1))
            valid = m.group(2)
            tail = ptxt[m.end():m.end() + 120]
            fein_m = pi.FEIN_RE.search(tail)
            candidates.append({
                "name": name,
                "fein": fein_m.group(1) if fein_m else None,
                "valid_for_bid": valid,
            })
        n_valid = sum(1 for c in candidates if c["valid_for_bid"] == "Yes")
        district_m = pi.DISTRICT_RE.search(ptxt)
        directory[cid] = {
            "candidates": candidates,
            "n_valid_candidates": n_valid,
            "competition_level": competition_level(n_valid) if n_valid > 0 else None,
            "district": district_m.group(1) if district_m else None,
        }
    return directory
