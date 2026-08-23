"""
Contractor name -> FEIN resolution.

Built from the same historical dataset the inference engine already trusts
(every FEIN that has ever appeared as a bidder or candidate). This is a lookup
layer only -- it never invents an identifier and never silently guesses.
"""
import json
import os
import pandas as pd

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
df = pd.read_csv(os.path.join(_THIS_DIR, "data", "indot_dataset.csv"))


def _all_known_names_feins():
    pairs = set()
    for _, row in df.iterrows():
        pairs.add((row.bidder_name.strip().upper(), row.bidder_fein))
    for _, row in df.iterrows():
        cands = json.loads(row.candidates_json)
        for c in cands:
            if c.get("fein") and c.get("name"):
                pairs.add((c["name"].strip().upper(), c["fein"]))
    return pairs


_NAME_FEIN_PAIRS = _all_known_names_feins()


def lookup_contractor(query_name):
    """Return (status, result).
    status = 'resolved'   -> result is {"fein": ..., "name": ...}
    status = 'ambiguous'  -> result is a list of candidate matches, caller must confirm
    status = 'not_found'  -> result is None
    """
    q = query_name.strip().upper()
    exact = [(n, f) for n, f in _NAME_FEIN_PAIRS if n == q]
    if len(exact) == 1:
        return "resolved", {"fein": exact[0][1], "name": exact[0][0]}
    if len(exact) > 1:
        # same name string mapped to >1 FEIN historically -- do NOT silently pick one
        return "ambiguous", [{"fein": f, "name": n} for n, f in exact]

    partial = [(n, f) for n, f in _NAME_FEIN_PAIRS if q in n]
    if len(partial) == 0:
        return "not_found", None
    if len(partial) == 1:
        # single partial match is still returned as ambiguous -- require explicit confirmation,
        # never silently proceed on a fuzzy match
        return "ambiguous", [{"fein": partial[0][1], "name": partial[0][0]}]
    return "ambiguous", [{"fein": f, "name": n} for n, f in sorted(set(partial))[:10]]


def confirm_contractor(fein, expected_name=None):
    """Explicit confirmation step -- returns True only if this FEIN is a known,
    resolvable identifier in the historical database."""
    known_feins = set(f for _, f in _NAME_FEIN_PAIRS)
    return fein in known_feins
