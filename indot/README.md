# QuoteConvert

**A pre-bid win-probability estimator for public highway construction contracts, built on real, verified competitor data from the Indiana Department of Transportation (INDOT).**

---

## A. The Problem

A construction contractor preparing a bid for a public infrastructure project has one central question: *if I submit this amount, how likely am I to win?* Bidding too high loses the job to a competitor; bidding too low wins the job at an unprofitable margin. Contractors currently answer this question from experience and gut feel — there is no standard, data-driven tool that turns public bidding history into a probability.

## B. Why Construction Bidding Is Hard to Model

A sealed-bid public auction looks simple (lowest valid bid wins) but is statistically difficult to predict from the outside, because the single largest driver of the outcome — **who else will actually bid, and how aggressively** — is normally invisible before the auction closes. Most public bid-tabulation datasets (including the majority of state DOT systems investigated during this project) only publish results *after* bidding closes: the winner, the losers, their prices. By then the information is useless for a contractor still deciding what to bid.

## C. QuoteConvert's Objective

Given a specific project, a specific contractor, and a proposed bid amount, estimate a calibrated probability of winning — using **only information that would genuinely have been available before the bid deadline**. Not a guess, not a guarantee: a defensible, validated statistical estimate.

## D. Key Insight: The Competition Field Matters Most

Across this project's research phase, one finding dominated every other experiment: **the single largest, most reliable driver of prediction quality was access to a real, pre-bid list of who is registered to compete on a specific project** — not clever modeling, not more generic features, not alternative machine-learning architectures. A baseline model using only bid/estimate ratio and generic contractor history reached AUC ≈ 0.64–0.70. Adding INDOT's real, published, pre-bid **Bidders & Planholders List** — the set of contractors who have formally registered to bid on *this specific* project before it closes — pushed that to AUC ≈ 0.72–0.98 depending on validation scale (see Results). Every other information source and model architecture tested (contractor-project fit, item-level scope features, conditional-logit reformulations, alternative states' data) either failed to add material value or could not be verified as genuinely pre-bid at usable scale. This is documented honestly below, including the negative results.

## E. Data Sources

- **INDOT (Indiana Department of Transportation)** — the sole production data source. Publishes, per letting: the Engineer's Estimate, a genuine pre-bid **Proposal Planholder List** (with a "Valid For Bid" flag distinguishing prime-eligible bidders from subcontractors/suppliers), and post-bid Official Bid Tabulations with every bidder's total and a persistent contractor identifier (FEIN). Verified recall of the pre-bid list against actual bidders: **99.9%** on the full validated dataset.
- **WSDOT (Washington State DOT)** — used in early research for deep historical contractor-behavior patterns, but does not publish a pre-bid candidate field. Confirmed, via direct A/B testing, to add no material value once INDOT's own candidate field and history are used — not included in the production model.
- Several other states/sources (Illinois, Texas, Florida, Oregon, Ohio) were investigated for a comparable pre-bid field. None were verified to provide one at usable scale with acceptable recall and historical depth (see Limitations).

## F. Model Architecture (Plain Language)

The model asks, for a specific candidate bid:

> *"Given this contractor, this project, this pre-bid list of who else is likely to compete, and this proposed bid amount — how likely is this bid to beat that competing field?"*

Concretely, four steps, each validated independently:

1. **Auction-consistent survival model** — for every other real, registered candidate on the project, the model looks at that specific contractor's own historical bidding pattern and asks "how likely is this competitor to bid higher than my candidate amount?" These are combined across the whole real candidate field into one probability that the candidate bid beats everyone.
2. **Contract-level normalization** — the probabilities for every candidate on the *same* project are rescaled so they are directly comparable to each other (this step alone materially improved calibration — see below).
3. **Temperature calibration** — a single scaling parameter, fit only on a held-out calibration period, sharpens or softens the normalized probabilities so they match observed outcomes.
4. **Output**: a calibrated probability, plus a bid-probability curve showing how that probability changes across nearby bid amounts.

## G. Probability Calibration

An earlier version of the model ranked candidates well (i.e., correctly identified who was *more* likely to win) but its raw probabilities were **not trustworthy in absolute terms** — the top-ranked candidate's true win rate was roughly double what the raw model claimed. This was diagnosed precisely: probabilities for different candidates on the same project didn't sum to a consistent total, so cross-candidate comparisons were distorted. The fix — per-contract normalization plus a temperature-scaling step, fit strictly on a held-out calibration window — closed most of this gap and is now part of the frozen production pipeline. This calibration-layer fix is one of the concrete technical contributions of this project, not merely a modeling footnote.

## H. Validation Methodology

- **Strictly time-based**: train, calibrate, and test periods are chronologically separated letting dates — never a random split. A contractor's history for any prediction only includes bids strictly before that prediction's date.
- **Leakage audited explicitly**: automated assertions confirm every contractor-history observation used in a prediction predates the prediction; the pre-bid candidate field is verified as genuinely time-stamped before the bid deadline (confirmed against the actual registration-deadline rule stated on INDOT's own registration forms).
- **Two validation rounds**: an initial 371-contract / 68-test-contract round, then a large-sample validation after recovering historical INDOT data back to 2019 (2,027 contracts / 431 test contracts, spanning 2019–2025, 5 districts). Every metric held or improved at the larger scale, with dramatically tighter confidence intervals — this is a meaningful, positive replication, not just "more data."
- **Multiple structurally different model formulations tested against the same data** (independent logistic, contract-level aggregate threshold, joint conditional-logit) all converged to the same performance band — evidence that the remaining constraint was sample size, not model choice.

## I. Results

| Metric | Value | What it means |
|---|---|---|
| Dataset size | 2,027 contracts | Total clean, QA-verified contracts, 2019–2025 |
| Test set | 431 contracts | Held out, never used for feature or calibration choices |
| **AUC (pooled, temperature-scaled)** | **≈ 0.980** | Rank-quality metric: how well the model orders winning vs. losing bids by probability. **AUC is not accuracy** — see Q10 below. |
| **Top-1 winner accuracy** | **≈ 90.0%** | The model's single highest-probability candidate was the *actual* winner in ~90% of test-set auctions. This is a distinct, more intuitive metric from AUC. |
| Within-contract AUC | ≈ 0.942 | AUC computed treating each project as its own competitive event (rather than pooling all bidder-rows together) |
| Multiclass LogLoss | ≈ 0.341 | Penalizes overconfident wrong probabilities; lower is better |
| Multiclass Brier | ≈ 0.192 | Mean squared error between predicted and actual outcome across all candidates in a contract; lower is better |
| Monotonicity violations | ≈ 5.5% | Fraction of within-contract candidate pairs where a lower bid did not receive a higher probability than a higher bid from a different contractor — expected residual, not a bug (see Limitations) |

**AUC ≈ 0.98 does not mean "98% accurate."** It means: if you randomly pick one winning bid and one losing bid from the test set, the model ranks the winner higher about 98% of the time. Top-1 accuracy (≈90%) is the closer analogue to "accuracy" in everyday language, and is reported separately for exactly that reason.

## J. Product Workflow (V1)

QuoteConvert V1 is **live INDOT competition intelligence with a validated bid win-probability engine** — not a fully autonomous "P(win) from INDOT data alone" generator. That distinction matters because of one verified fact: **INDOT does not publish its Engineer's Estimate before bidding closes** (checked directly against every plausible pre-bid document — Planholder List, Notice to Contractors, SOPI — none contain it; see K.2). So V1 splits into two modes:

**Deployment architecture — Streamlit is decoupled from INDOT.** Local testing confirmed Streamlit's own outbound connection to `www.in.gov` is not reliable, while GitHub Actions' is (validated live, ~15s Stage 1 runs). So a scheduled GitHub Actions workflow (`indot_live_refresh.yml`) is the *only* component that talks to INDOT: it runs discovery + Planholder ingestion, then writes a versioned **live snapshot** (`data/live_snapshot.json`, no FEIN/PII, committed to git only when its content actually changed). The Streamlit app reads that snapshot from local disk — it never calls INDOT directly, so it keeps working even if `www.in.gov` is completely unreachable from wherever the app happens to run. A second, non-public file (`data/live_snapshot_internal.json`) carries the FEIN each contractor needs to be matched against their own bidding history inside the frozen inference engine; it is read only by Mode B's prediction logic, never rendered.

**Mode A — Live Competition Intelligence** (fully automatic, no user input required):
1. The system automatically discovers the next upcoming INDOT letting(s) and checks whether INDOT has published that letting's Planholder List
2. If published, it downloads and parses the real, current, pre-bid "Valid For Bid" candidate list for every contract in that letting
3. It shows, per contract: pre-bid candidate count and competition level (Low/Moderate/High) — entirely from live, automatically-collected data

**Mode B — Win-Probability Analysis** (requires three user inputs):
1. Contractor — chosen from that project's live, currently-published Valid-For-Bid list (not free text)
2. Reference Estimate ($) — supplied by the contractor, since INDOT's own Engineer's Estimate is not publicly available before bidding (the historical model was trained on INDOT's EE; a different reference estimate may affect calibration — this is disclosed directly in the UI)
3. Candidate Bid ($) — the amount being considered
4. The system computes `bid_ratio = candidate_bid / reference_estimate` and passes it, unmodified, into the frozen inference engine, returning: estimated P(win), Bid/Reference-Estimate ratio, competition level, a data-quality state, and a bid-probability curve across nearby bid amounts

Regression-verified with a real historical example: contract `B-40989-A` (Dec 13, 2023 letting), reconstructed strictly as it would have appeared before its own bid deadline using its real Engineer's Estimate, returning a coherent, monotonic, well-calibrated result (P(win) = 93.2% for the eventual actual winner's real bid) — kept in the app as a regression-test tab, not part of the V1 product surface.

## K. Limitations

1. **INDOT does not publish its Engineer's Estimate before bidding** — verified directly, not assumed (see L). V1 therefore asks the contractor for a Reference Estimate rather than fabricating or scraping a post-bid figure.
2. **Using a Reference Estimate other than INDOT's own Engineer's Estimate can affect calibration** — the historical model was trained and validated using INDOT's own EE; a materially different reference estimate is disclosed to the user as a source of possible error, not silently absorbed.
3. **Competition-aware analysis absolutely requires INDOT to have published the Planholder List for a project** — if it has not yet been published, the system says so explicitly rather than guessing or reusing an older candidate list.
4. **Automatic discovery is bounded to the nearest upcoming letting for full per-contract detail** — by design, to avoid unnecessary requests to INDOT (see DOCS.md architecture); additional upcoming lettings are shown with less detail until they become the nearest one.
5. **Live Competition reflects the last successful snapshot refresh, not the current instant** — the app shows the snapshot's own retrieval timestamp, and flags it explicitly ("Live data may be stale.") once it exceeds a freshness threshold, rather than silently presenting old data as current.
6. **Private contractor economics are not observable** — internal cost structure, capacity, and strategy are not visible in any public data source and cannot be modeled directly.
7. **Extreme or unusual bid ratios may be less reliable** — the model flags bids outside the historically well-represented range rather than pretending equal confidence everywhere.
8. **This is a probability estimate, not a guarantee.** No model can eliminate genuine auction uncertainty.
9. **No "optimal bid" recommendation is included in V1** — see Section on this decision below.

## L. Engineer's Estimate: Verified Not Publicly Available Pre-Bid

An exhaustive, direct check (not an assumption) of every plausible pre-bid INDOT source — the Proposal Planholder List, the Notice to Contractors, the Schedule of Pay Items (SOPI) — found the Engineer's Estimate in **none of them**; it appears only in the *post-bid* Official Bid Tabulation. A follow-up research experiment tested whether a "Shadow EE" could be predicted from pre-bid project-scope features (SOPI item counts/quantities) well enough to substitute for the real thing: it could not — the best model available (RandomForest) still had 36% median absolute percentage error, and substituting it into the frozen P(win) engine dropped pooled AUC from 0.725 to 0.581 on the same held-out test population (a decisive degradation, not a minor one). A "skip EE entirely" alternative was also tested and rejected — it achieves near-perfect *within-contract* ranking only by mechanically exploiting "lowest raw dollar bid wins," which fails completely (AUC ≈ 0.55) at the actual job of comparing bids across differently-sized projects. Both experiments are why V1 asks the contractor for a Reference Estimate instead of pretending to obtain INDOT's own.

## L2. Snapshot Freshness, Provenance, and No Silent Fallback

Every snapshot records its own `retrieved_at` timestamp, the Planholder document's own retrieval timestamp and SHA-256 checksum, and the source URLs used to obtain it — all shown directly in the Live Competition tab. If the scheduled refresh has not produced a snapshot at all (missing file), or produces one that fails schema validation, the app shows an explicit `refresh_failed` state rather than falling back to the historical dataset. If the most recent successful snapshot is older than the freshness threshold, the app shows it with an explicit "Live data may be stale" warning rather than silently presenting it as current. A snapshot showing `no_prebid_candidate_list` or `no_upcoming_letting` is never replaced with an older, previously-cached candidate list.

## M. How to Run Locally

```bash
cd indot
python3 -m streamlit run app.py
```
Requires: `pandas`, `numpy`, `streamlit`, `pypdf` (see the module imports; no other dependencies). Opens a local web UI with three tabs: **Live Competition** (Mode A), **Win-Probability Analysis** (Mode B), and a **Historical Demo** regression-test tab defaulting to the validated demo contract `B-40989-A`.

**The app itself never calls INDOT.** Modes A and B read `data/live_snapshot.json` (public) and `data/live_snapshot_internal.json` (adds FEIN, used only internally for Mode B's inference call) from local disk. Those files are produced by a separate scheduled process:

```bash
cd indot
python3 generate_snapshot.py   # requires real outbound access to www.in.gov -- run via GitHub Actions
```

The GitHub Actions workflow `.github/workflows/indot_live_refresh.yml` runs this daily (and on manual `workflow_dispatch`), committing the two snapshot files back to the repo **only when their content actually changed**. It never runs inference and is the only component in this project with INDOT network access. If no snapshot has ever been generated, both tabs show an explicit `refresh_failed` state — never a silent fallback to historical data.

---

## Why We Did Not Build "Optimal Bid" (Section 8 requirement)

An earlier phase of this project directly tested expected-value (EV) maximization — bid amount × margin × P(win) — as a candidate recommendation engine. It was found to be **structurally degenerate**: the optimal EV bid consistently hit the edge of whatever search range was tested, regardless of the cost assumption used, because the P(win) curve decays more gently than margin grows. This is a genuine, tested finding, not a guess. Rather than paper over that with an arbitrary constraint, V1 deliberately ships **P(win) + the bid curve** and leaves the bidding decision to the contractor — a defensible product boundary, not a missing feature.

---

# Final Project Summary

**One paragraph**: QuoteConvert automatically discovers upcoming INDOT lettings and their real, live, pre-bid competitor registration lists, then estimates a contractor's probability of winning a proposed bid against that live competitive field — using an auction-consistent statistical model validated on 2,027 real historical contracts (AUC ≈ 0.98, ~90% top-1 winner accuracy); because INDOT does not publish its own Engineer's Estimate before bidding closes (verified directly, and confirmed unfixable by prediction — see K/L), the contractor supplies their own Reference Estimate for the ratio calculation, with that tradeoff disclosed plainly in the product.

**Three paragraphs**:
Contractors bidding on public construction projects currently have no data-driven way to estimate their odds before submitting a price — public bid-tabulation data is almost always published only after bidding closes, by which point it's useless for the decision at hand.

QuoteConvert's core discovery, made after testing multiple states and multiple modeling approaches, was that Indiana's DOT uniquely publishes a genuine pre-bid list of registered competitors for each project — and that this single piece of information, combined with each contractor's own historical bidding behavior through an auction-consistent statistical model, is dramatically more predictive than any generic feature-engineering approach tried. A calibration-layer fix (per-contract normalization plus temperature scaling) further closed a real gap between the model's *ranking* quality and the *trustworthiness* of its raw probabilities.

The result is a working product that automatically discovers upcoming INDOT lettings and their live competition field with zero user input (Mode A), and lets a contractor enter a Reference Estimate and candidate bid to get a calibrated win probability and bid curve against that live field (Mode B) — deliberately not pretending to auto-obtain INDOT's own Engineer's Estimate, since a dedicated experiment confirmed that predicting it well enough to substitute safely is not currently achievable (see L).

**30-second version**: *"QuoteConvert automatically watches INDOT for upcoming projects and shows a contractor the real, live list of who's registered to compete — before bidding closes. Enter your own cost estimate and a candidate bid, and it returns a calibrated probability of winning, validated on over 2,000 real historical contracts at 90% top-1 accuracy. It doesn't guess INDOT's own estimate — we tested that directly and it degrades the result, so we ask the contractor for theirs instead and say so plainly."*
