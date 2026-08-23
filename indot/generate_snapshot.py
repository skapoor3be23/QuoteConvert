"""
Generates the two live-snapshot files consumed by app.py:

  data/live_snapshot.json          -- PUBLIC. No FEIN, no PII. Committed to git.
  data/live_snapshot_internal.json -- INTERNAL. Adds FEIN + district so
                                       Win-Probability mode can still call the
                                       frozen inference engine (which requires
                                       FEIN to key contractor history). Never
                                       rendered directly to the UI.

Run ONLY from an environment with real outbound access to INDOT (GitHub
Actions) -- see .github/workflows/indot_live_refresh.yml. Imports (never
modifies) live_connector.py, parse_indot.py, live_ui_logic.py.

Exit codes:
  0 -- snapshot written. This includes the legitimate non-error outcomes
       status=no_upcoming_letting and status=no_prebid_candidate_list.
  1 -- a genuine failure (network error, invalid PDF, unparseable download).
       Nothing is written in this case -- the caller (the workflow) must
       leave the previously committed snapshot untouched and must not commit.
"""
import json
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from live_connector import discover_upcoming_lettings, ingest_earliest_planholder_list, LETTING_ARCHIVE_URL
import live_ui_logic as ui

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
PUBLIC_PATH = os.path.join(DATA_DIR, "live_snapshot.json")
INTERNAL_PATH = os.path.join(DATA_DIR, "live_snapshot_internal.json")


def _write_json_atomic(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2)
    os.replace(tmp, path)


def main():
    os.makedirs(DATA_DIR, exist_ok=True)
    now_iso = datetime.now(timezone.utc).isoformat()

    try:
        results, _index_entry = discover_upcoming_lettings()
    except Exception as e:
        print(f"FAILURE: discovery raised an exception: {e}", file=sys.stderr)
        return 1

    if not results:
        public = ui.build_snapshot(now_iso, LETTING_ARCHIVE_URL, None, None, ui.SNAPSHOT_STATUS_NO_UPCOMING)
        internal = ui.build_snapshot(now_iso, LETTING_ARCHIVE_URL, None, None, ui.SNAPSHOT_STATUS_NO_UPCOMING,
                                      include_fein=True)
        ui.validate_public_snapshot_schema(public)
        _write_json_atomic(PUBLIC_PATH, public)
        _write_json_atomic(INTERNAL_PATH, internal)
        print("no upcoming letting found -- snapshot written with status=no_upcoming_letting")
        return 0

    earliest = results[0]
    if not earliest["planholder_list_available"]:
        public = ui.build_snapshot(now_iso, LETTING_ARCHIVE_URL, earliest["letting_date"],
                                    earliest["letting_page_url"], ui.SNAPSHOT_STATUS_NO_PREBID)
        internal = ui.build_snapshot(now_iso, LETTING_ARCHIVE_URL, earliest["letting_date"],
                                      earliest["letting_page_url"], ui.SNAPSHOT_STATUS_NO_PREBID, include_fein=True)
        ui.validate_public_snapshot_schema(public)
        _write_json_atomic(PUBLIC_PATH, public)
        _write_json_atomic(INTERNAL_PATH, internal)
        print(f"earliest upcoming letting ({earliest['letting_date']}) has no Planholder List yet -- "
              "snapshot written with status=no_prebid_candidate_list")
        return 0

    try:
        report = ingest_earliest_planholder_list()
    except Exception as e:
        print(f"FAILURE: ingestion raised an exception: {e}", file=sys.stderr)
        return 1

    if report["status"] != "ok":
        print(f"FAILURE: ingestion did not succeed (status={report['status']}) -- "
              "leaving the existing committed snapshot untouched, not writing a replacement", file=sys.stderr)
        return 1

    try:
        directory = ui.build_contract_directory(report["local_path"])
    except Exception as e:
        print(f"FAILURE: could not parse downloaded Planholder PDF: {e}", file=sys.stderr)
        return 1

    common_kwargs = dict(
        planholder_url=report["planholder_url"],
        planholder_retrieved_at=report["retrieval_timestamp"],
        planholder_sha256=report["pdf_sha256"],
        candidate_count=report["candidate_count"],
        valid_for_bid_count=report["valid_for_bid_count"],
        directory=directory,
    )
    public = ui.build_snapshot(now_iso, LETTING_ARCHIVE_URL, report["letting_date"], earliest["letting_page_url"],
                                ui.SNAPSHOT_STATUS_AVAILABLE, include_fein=False, **common_kwargs)
    internal = ui.build_snapshot(now_iso, LETTING_ARCHIVE_URL, report["letting_date"], earliest["letting_page_url"],
                                  ui.SNAPSHOT_STATUS_AVAILABLE, include_fein=True, **common_kwargs)

    ui.validate_public_snapshot_schema(public)

    _write_json_atomic(PUBLIC_PATH, public)
    _write_json_atomic(INTERNAL_PATH, internal)

    print(f"snapshot written: letting_date={report['letting_date']} "
          f"candidate_count={report['candidate_count']} valid_for_bid_count={report['valid_for_bid_count']} "
          f"planholder_sha256={report['pdf_sha256']} retrieved_at={now_iso}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
