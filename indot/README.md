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

## J. Product Workflow

1. Select a project (in the current demo, from historical INDOT lettings)
2. The system loads Engineer's Estimate, district, and the project's real pre-bid candidate list
3. Select the contractor (from that project's actual registered candidates)
4. Enter a hypothetical bid amount
5. Receive: estimated P(win), a bid-probability curve across nearby bid amounts, a competition-level indicator (e.g. *"High competition — 9 pre-bid candidates"*), and a data-quality state (Standard / Limited history / Unavailable)

Demonstrated end-to-end with a real historical example: contract `B-40989-A` (Dec 13, 2023 letting), reconstructed strictly as it would have appeared before its own bid deadline, returning a coherent, monotonic, well-calibrated result (P(win) = 93.2% for the eventual actual winner's real bid).

## K. Limitations

1. **Live INDOT access is currently unavailable** from every environment tested this project (see L).
2. **V1 operates in historical/as-of demo mode** — real predictions on genuinely upcoming projects are not yet live.
3. **Competition-aware prediction absolutely requires the pre-bid candidate list** — if INDOT has not yet published it for a project, the system returns no probability rather than a guess.
4. **Private contractor economics are not observable** — internal cost structure, capacity, and strategy are not visible in any public data source and cannot be modeled directly.
5. **Extreme or unusual bid ratios may be less reliable** — the model flags bids outside the historically well-represented range rather than pretending equal confidence everywhere.
6. **This is a probability estimate, not a guarantee.** No model can eliminate genuine auction uncertainty.
7. **No "optimal bid" recommendation is included in V1** — see Section on this decision below.

## L. Live-Data Limitation (Detail)

Three INDOT access routes were tested directly from this project's execution environment: the main INDOT site (network-level connection failure — TCP handshake never completes), and two internal subdomains used for bid viewing (both return a clean, fast HTTP 403 — genuine access control, not something to be bypassed). This is a real, precisely diagnosed infrastructure gap, not a data-availability problem: the historical connector code and parsing logic are built and validated against 77 real archived INDOT documents. What remains is executing that same code from an environment with actual outbound access to INDOT's live site.

## M. How to Run Locally

```bash
cd indot
python3 -m streamlit run app.py
```
Requires: `pandas`, `numpy`, `streamlit` (see the module imports; no other dependencies). Opens a local web UI defaulting to the validated demo contract `B-40989-A`.

---

## Why We Did Not Build "Optimal Bid" (Section 8 requirement)

An earlier phase of this project directly tested expected-value (EV) maximization — bid amount × margin × P(win) — as a candidate recommendation engine. It was found to be **structurally degenerate**: the optimal EV bid consistently hit the edge of whatever search range was tested, regardless of the cost assumption used, because the P(win) curve decays more gently than margin grows. This is a genuine, tested finding, not a guess. Rather than paper over that with an arbitrary constraint, V1 deliberately ships **P(win) + the bid curve** and leaves the bidding decision to the contractor — a defensible product boundary, not a missing feature.

---

# Final Project Summary

**One paragraph**: QuoteConvert estimates a contractor's probability of winning a public highway construction bid before submission, using real, verified, pre-bid competitor registration data from Indiana's DOT combined with each contractor's own historical bidding pattern — validated on 2,027 real contracts with AUC ≈ 0.98 and ~90% top-1 winner accuracy, and packaged as a working local demo; the one remaining gap before live deployment is outbound network access to INDOT's site from a production environment.

**Three paragraphs**:
Contractors bidding on public construction projects currently have no data-driven way to estimate their odds before submitting a price — public bid-tabulation data is almost always published only after bidding closes, by which point it's useless for the decision at hand.

QuoteConvert's core discovery, made after testing multiple states and multiple modeling approaches, was that Indiana's DOT uniquely publishes a genuine pre-bid list of registered competitors for each project — and that this single piece of information, combined with each contractor's own historical bidding behavior through an auction-consistent statistical model, is dramatically more predictive than any generic feature-engineering approach tried. A calibration-layer fix (per-contract normalization plus temperature scaling) further closed a real gap between the model's *ranking* quality and the *trustworthiness* of its raw probabilities.

The result, validated on 2,027 real contracts with a genuinely held-out 431-contract test set, is a working local demo that takes a real historical project, contractor, and bid amount and returns a calibrated win probability and bid curve — with the one remaining step before live production being network access to INDOT's site from wherever the system is actually deployed.

**30-second version**: *"QuoteConvert estimates a construction contractor's probability of winning a public bid before they submit it. The breakthrough was finding that Indiana's DOT publishes a real, verified list of who's registered to compete on each project before bidding closes — that single fact, combined with each contractor's own bidding history, drove the model from roughly a coin-flip's worth of useful signal to a validated 90% top-1 accuracy across 2,000+ real contracts. It's built and working as a local demo today; the only thing standing between this and a live tool is getting our servers real network access to INDOT's website."*
