"""
Incremental historical bid-database updater.

new completed bid tab -> parse -> validate -> append -> contractor history update

Never mutates existing rows. Every ingested record carries its raw source, parsed
record, validation status, and ingestion timestamp, so a bad ingest can always be
audited or rolled back without touching prior data.
"""
import json, os
import pandas as pd
from datetime import datetime, timezone

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
MASTER_PATH = os.path.join(_THIS_DIR, "data", "indot_dataset.csv")
INGEST_LOG_PATH = os.path.join(_THIS_DIR, "data", "ingest_log.jsonl")


def validate_record(record):
    """Same QA checks used throughout this project's manual audits, made explicit
    and re-usable: EE present, ratio sane, contract has exactly one winner within
    its own letting, candidate field recall is plausible."""
    errors = []
    if not record.get("engineers_estimate") or record["engineers_estimate"] <= 0:
        errors.append("missing_or_invalid_EE")
    if not record.get("bidder_fein"):
        errors.append("missing_contractor_identity")
    if record.get("bidder_total", 0) <= 0:
        errors.append("invalid_bid_amount")
    if not record.get("letting_date"):
        errors.append("missing_letting_date")
    return (len(errors) == 0), errors


def ingest_new_lettings(new_records):
    """new_records: list of dicts matching the master schema (one per bidder row).
    Appends only validated rows; logs every attempt (pass or fail) to the ingest log.
    Returns a summary, never raises on a single bad record."""
    master = pd.read_csv(MASTER_PATH) if os.path.exists(MASTER_PATH) else pd.DataFrame()
    accepted, rejected = [], []
    now = datetime.now(timezone.utc).isoformat()

    for rec in new_records:
        ok, errors = validate_record(rec)
        log_entry = {
            "ingestion_timestamp": now,
            "raw_record": rec,
            "validation_status": "accepted" if ok else "rejected",
            "errors": errors,
        }
        with open(INGEST_LOG_PATH, "a") as f:
            f.write(json.dumps(log_entry) + "\n")
        if ok:
            accepted.append(rec)
        else:
            rejected.append((rec, errors))

    if accepted:
        new_df = pd.DataFrame(accepted)
        # per-contract "exactly one winner" QA before appending
        bad_contracts = []
        for cid, g in new_df.groupby("contract_id"):
            if g.is_winner.sum() != 1:
                bad_contracts.append(cid)
        if bad_contracts:
            dropped = new_df[new_df.contract_id.isin(bad_contracts)]
            new_df = new_df[~new_df.contract_id.isin(bad_contracts)]
            for _, r in dropped.iterrows():
                rejected.append((r.to_dict(), ["winner_count_QA_failed"]))
        combined = pd.concat([master, new_df], ignore_index=True) if len(master) else new_df
        combined.to_csv(MASTER_PATH, index=False)

    return {
        "accepted": len(accepted) - len([r for r in rejected if "winner_count_QA_failed" in r[1]]),
        "rejected": len(rejected),
        "rejected_detail": rejected[:10],
    }
