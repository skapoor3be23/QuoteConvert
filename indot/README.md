# QuoteConvert

**Live INDOT competition intelligence with a validated bid win-probability engine.**

---

## 1. Problem

Contractors bidding on public highway construction projects have no data-driven way to estimate their odds before submitting a price. Public bid-tabulation data is almost always published only *after* bidding closes — by which point it's useless for the decision at hand.

## 2. Solution

QuoteConvert automatically discovers upcoming INDOT (Indiana Department of Transportation) lettings and their real, live, pre-bid list of registered competitors, then estimates a contractor's probability of winning a proposed bid against that live competitive field — using an auction-consistent statistical model validated on 2,027 real historical contracts.

This is **live INDOT competition intelligence with a validated bid win-probability engine** — not a fully autonomous "P(win) from INDOT data alone" generator, and not a guaranteed prediction. Two things are automated (competition discovery) and one thing is user-supplied (a Reference Estimate), for reasons explained in Section 11.

## 3. Architecture

```
GitHub Actions (indot_live_refresh.yml -- daily + workflow_dispatch,
the ONLY component with real outbound access to www.in.gov)
        |
        v
generate_snapshot.py
  Stage 1: discover the nearest upcoming letting(s)
  Stage 2: download + parse the earliest upcoming letting's Planholder PDF
        |
        v
data/live_snapshot.json           (public, no FEIN/PII -- committed to git)
data/live_snapshot_internal.json  (adds FEIN + district -- for inference only)
        |
        v  (git commit, only if content changed)
=====================================================================
Streamlit app (app.py) -- reads the committed snapshot from local disk.
Works even if www.in.gov is unreachable from wherever it runs.
=====================================================================
        |
        v
Mode A: Live Competition   -- automatic, zero user input
Mode B: Win-Probability    -- Contractor + Reference Estimate + Candidate Bid
        |
        v
frozen inference.py  ->  P(win) + bid-probability curve
```

## 4. Live INDOT Data Pipeline

A scheduled GitHub Actions workflow (`.github/workflows/indot_live_refresh.yml`) is the only component in this project with INDOT network access:

- **Discovery** — finds the nearest upcoming letting(s) from INDOT's archive index, validated live: completed in **~15 seconds**.
- **Ingestion** — downloads and parses the earliest upcoming letting's Planholder (Bidders & Planholders) List, reusing the same validated parser confirmed against a real document (**48 contracts, 238 Valid-For-Bid candidates**; golden contract `R-43365-A` has **9 Valid-For-Bid candidates**).
- **Snapshot** — writes a versioned, sanitized snapshot (no FEIN, no PII) to `data/live_snapshot.json`, committing it back to the repo only when the content actually changed. A full live snapshot refresh completed in **~17 seconds**.
- Streamlit never calls INDOT directly — it only reads the committed snapshot from disk, so the app keeps working even when `www.in.gov` is unreachable from wherever it happens to run.

## 5. Statistical Model

For a candidate bid, the frozen model asks: *"Given this contractor, this project, the real pre-bid list of who else is likely to compete, and this proposed bid amount — how likely is this bid to beat that competing field?"*

1. **Auction-consistent survival model** — for every other real, registered candidate, the model uses that contractor's own historical bidding pattern to estimate the odds they bid higher than the candidate amount, combined across the whole real candidate field.
2. **Contract-level normalization** — rescales every candidate's probability on the same project so they are directly comparable.
3. **Temperature calibration** — a single parameter, fit only on a held-out calibration period, corrects the sharpness of the normalized probabilities.
4. **Output** — a calibrated P(win) plus a monotonic bid-probability curve across nearby bid amounts.

## 6. Validation Results

| Metric | Value |
|---|---|
| Historical dataset | **2,027 contracts** |
| Chronological test set | **431 contracts**, held out, never used for feature or calibration choices |
| AUC (pooled, temperature-scaled) | ≈ 0.980 |
| Top-1 winner accuracy | ≈ 90.0% |
| Frozen regression example (`R-43365-A`, contractor PAUL H ROHE COMPANY INC) | **p_win = 0.999499**, reproduced byte-for-byte across the live-connector and app test suites |

**AUC ≈ 0.98 does not mean "98% accurate."** It means: given one random winning bid and one random losing bid from the test set, the model ranks the winner higher about 98% of the time. Top-1 accuracy (≈90%) is the closer everyday analogue to "accuracy" and is reported separately for exactly that reason.

## 7. Data Leakage Controls

For any prediction made "as of" a bid date **D**:

| Allowed | Forbidden |
|---|---|
| Project info known before D | Current bid amount of any other candidate |
| Pre-bid candidate list, verified published before D's bid deadline | The actual winner of contract D |
| Contractor history strictly **before** D (`< D`, never `<=`) | Any contractor observation dated on or after D |

Enforced in code (`build_history_snapshot` filters strictly by `< as_of_date`) and checked by an automated assertion suite, not merely assumed.

## 8. Current Product Behavior

- **Mode A — Live Competition** (automatic, no input required): shows the nearest upcoming INDOT letting, its Valid-For-Bid candidate count per contract, and competition level (Low/Moderate/High), sourced entirely from the committed live snapshot.
- **Mode B — Win-Probability Analysis** (3 user inputs: Contractor, Reference Estimate, Candidate Bid): computes `bid_ratio = candidate_bid / reference_estimate` and passes it, unmodified, into the frozen `inference.infer()`.
- **Historical Demo** (regression-test tab, not part of the product surface): reconstructs a real historical contract "as of" its own bid deadline, used only to confirm the frozen engine still produces identical output.
- Explicit app states — `live_candidates_available`, `no_prebid_candidate_list`, `no_upcoming_letting`, `stale_snapshot`, `refresh_failed` — are shown as distinct, intentional product states; the app never silently substitutes historical data for missing or stale live data.

## 9. Limitation: Pre-Bid INDOT Engineer's Estimate Unavailable

INDOT does not publicly provide its Engineer's Estimate before bidding closes — checked directly against every plausible pre-bid document (Planholder List, Notice to Contractors, Schedule of Pay Items); it appears only in the post-bid Official Bid Tabulation. Because of this, QuoteConvert:

- does **not** scrape or use the post-bid Engineer's Estimate for a live prediction,
- does **not** fabricate an estimate,
- does **not** substitute historical bidder data for missing live pre-bid data,
- **uses a user-supplied Reference Estimate** for live P(win) instead (Section 10).

A dedicated experiment tested whether a predicted "Shadow EE" (from pre-bid project-scope features) could substitute for the real, unavailable Engineer's Estimate: it was **rejected** — the best model available still had large error, and substituting it into the frozen P(win) engine dropped pooled AUC from 0.725 to 0.581 on the same held-out test population, a decisive downstream degradation, not a minor one. A "skip EE entirely" alternative was also tested and rejected (near-chance pooled AUC once the mechanical "lowest bid wins" tautology is accounted for). Both negative results are why Mode B asks the contractor for a Reference Estimate instead of pretending to obtain INDOT's own.

## 10. Reference Estimate Workflow

1. Contractor selects a live, currently-published Valid-For-Bid project from Mode A.
2. Contractor enters their own **Reference Estimate ($)** — the UI discloses: *"QuoteConvert's historical model was trained using INDOT Engineer's Estimates; a different reference estimate may affect calibration."*
3. Contractor enters a **Candidate Bid ($)**.
4. `bid_ratio = candidate_bid / reference_estimate` is passed into the unmodified frozen engine.
5. If the selected contractor is not on the current Valid-For-Bid list, no probability is produced.

## 11. Deployment

- **Compute**: local Streamlit (`streamlit run app.py`); no server-side INDOT network dependency.
- **Live data**: GitHub Actions (`indot_live_refresh.yml`), `workflow_dispatch` + daily schedule, `permissions: contents: write`, commits the snapshot only when its content changed.
- **Storage**: two JSON snapshot files committed to the repo (`data/live_snapshot.json` public, `data/live_snapshot_internal.json` internal-only, never rendered).
- **Model**: frozen, versioned artifact (`data/model_artifact.json`, `model_version: indot_survival_v1.0`), loaded at runtime, never hard-coded.

## 12. How to Run Locally

```bash
git clone <repository-url>
cd indot
pip install -r ../requirements.txt
streamlit run app.py
```

The app reads `data/live_snapshot.json` from disk — it does not need INDOT network access to run. If no snapshot has ever been generated, the Live Competition and Win-Probability tabs show an explicit `refresh_failed` state rather than silently falling back to historical data. The **Historical Demo** tab always works offline, using the validated historical dataset.

## 13. Repository Structure

```
indot/
  app.py                    Streamlit UI (Mode A / Mode B / Historical Demo)
  inference.py               Frozen P(win) engine -- deterministic, leakage-safe
  live_connector.py           INDOT discovery + Planholder ingestion (network-facing)
  generate_snapshot.py         Builds the two live-snapshot files (run via GitHub Actions)
  live_ui_logic.py              Pure app-state / snapshot-schema / validation logic
  parse_indot.py                 Validated Planholder/Bid-Tabulation PDF parser
  contractor_lookup.py            Contractor name -> FEIN resolution utility (not yet wired into app.py)
  history_updater.py               Incremental historical bid-database updater (not yet wired into app.py)
  data/
    indot_dataset.csv               2,027-contract validated historical dataset
    model_artifact.json              Frozen model config (thresholds, calibration, version)
    live_snapshot.json                Public live snapshot (committed by CI)
    live_snapshot_internal.json        Internal live snapshot (FEIN, for inference only)
    regression_fixture.json            Fixed input/output pair for reproducibility tests
  tests/                              Regression + unit test suites (see Section 6 numbers)
  research/
    shadow_ee/                        Shadow-EE experiment (isolated, rejected -- Section 9)
    legacy_scripts/                    Early scraping/audit dev scripts, superseded
    legacy_raw_documents/               Archived raw PDFs/HTML/Wayback-CDX caches
    legacy_datasets/                     Superseded intermediate CSV/JSON research datasets
    legacy_duplicates/                   Stale duplicate files found during repository cleanup
.github/workflows/
  indot_live_refresh.yml              Daily + manual live snapshot refresh (the only INDOT-connected job)
  indot_upcoming_discovery.yml         Earlier discovery+ingestion validation workflow
  indot_connectivity_test.yml          Earlier connectivity diagnostic workflow
```

`contractor_lookup.py` and `history_updater.py` are complete, working utility modules from the original planned architecture; they are not currently called by `app.py` and are kept as documented, ready-to-wire components rather than removed.

## 14. Demo / Deployed App Link

There is no publicly hosted deployment yet — QuoteConvert currently runs as a local Streamlit app (Section 12) reading a live snapshot that GitHub Actions keeps refreshed in the repository. This section will be updated with a live link once a public deployment exists; no placeholder or unverified URL is listed here.

---

## Why We Did Not Build "Optimal Bid"

An earlier phase of this project directly tested expected-value (EV) maximization — bid amount × margin × P(win) — as a candidate recommendation engine. It was found to be **structurally degenerate**: the optimal EV bid consistently hit the edge of whatever search range was tested, because the P(win) curve decays more gently than margin grows. Rather than paper over that, V1 ships **P(win) + the bid curve** and leaves the bidding decision to the contractor.

## Final Project Summary

**One paragraph**: QuoteConvert automatically discovers upcoming INDOT lettings and their real, live, pre-bid competitor registration lists, then estimates a contractor's probability of winning a proposed bid against that live competitive field — using an auction-consistent statistical model validated on 2,027 real historical contracts (AUC ≈ 0.98, ~90% top-1 winner accuracy); because INDOT does not publish its own Engineer's Estimate before bidding closes (verified directly, and confirmed unfixable by prediction), the contractor supplies their own Reference Estimate for the ratio calculation, with that tradeoff disclosed plainly in the product.

**30-second version**: *"QuoteConvert automatically watches INDOT for upcoming projects and shows a contractor the real, live list of who's registered to compete — before bidding closes. Enter your own cost estimate and a candidate bid, and it returns a calibrated probability of winning, validated on over 2,000 real historical contracts at 90% top-1 accuracy. It doesn't guess INDOT's own estimate — we tested that directly and it degrades the result, so we ask the contractor for theirs instead and say so plainly."*

See `DOCS.md` for the full architecture diagram, interview Q&A, demo script, and detailed project status table.
