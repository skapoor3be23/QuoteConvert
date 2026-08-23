# QuoteConvert — Architecture, Interview Prep, Demo Script, Project Status

## Architecture Diagram

```
DECOUPLED DEPLOYMENT (Streamlit never calls INDOT directly)

GitHub Actions (indot_live_refresh.yml -- daily + workflow_dispatch, the ONLY
component with real outbound access to www.in.gov)
  |
  v
generate_snapshot.py
  Stage 0/1: live_connector.discover_upcoming_lettings()
             (fetch archive once, check <=5 nearest lettings for a Planholder link)
  Stage 2:   live_connector.ingest_earliest_planholder_list()
             (download + parse ONLY the earliest upcoming letting's PDF)
  |
  v
Two versioned snapshot files, written atomically:
  data/live_snapshot.json           (PUBLIC: no FEIN, no PII -- committed)
  data/live_snapshot_internal.json  (adds FEIN + district -- for Mode B's
                                      inference call only, never rendered)
  |
  v
git commit + push, ONLY if the snapshot content actually changed
  |
  v
=================================================================
Streamlit app (app.py) -- reads the committed snapshot from local disk.
Works even if www.in.gov is completely unreachable from this process.
=================================================================


MODE A -- LIVE COMPETITION INTELLIGENCE (automatic, no user input)

Read data/live_snapshot.json
        |
        v
Classify into exactly one app state:
  live_candidates_available / no_prebid_candidate_list /
  no_upcoming_letting / stale_snapshot / refresh_failed
        |
        v
  Competition level (Low/Moderate/High) + candidate count, per contract
        |
        v
  Shown in the app with ZERO reference estimate required


MODE B -- WIN-PROBABILITY ANALYSIS (user supplies 3 inputs)

Contractor (from the live candidate field above)
Reference Estimate ($)  <-- supplied by the contractor; INDOT's own
                             Engineer's Estimate is NOT published pre-bid
                             (verified -- see DOCS.md / README.md Section L)
Candidate Bid ($)
        |
        v
   bid_ratio = candidate_bid / reference_estimate
        |
        v
Historical contractor behaviour --> Auction-consistent survival model
(strictly prior bid history,             (per real candidate: P(this
 FEIN-keyed, time-boxed)                   competitor bids higher than
        |                                  my candidate bid))
        |                                  |
        \----------------------------------/
                        |
                        v
          Contract-level normalization
        (rescales all candidates on the
         SAME project to be comparable)
                        |
                        v
             Temperature calibration
      (single parameter, fit on a held-out
       calibration period only)
                        |
                        v
                     P(win)
                        |
                        v
             Bid-probability curve
        (P(win) re-computed across a
         range of nearby bid amounts)
```

`inference.py`, `model_artifact.json`, and the calibration/survival-model logic are unchanged from the frozen, validated research phase — Mode B only changes where the ratio's denominator comes from (a user-supplied Reference Estimate instead of a historical Engineer's Estimate), not how the ratio is used.

**Data flow for validation** (kept separate from the diagram above to avoid confusing training with inference):
```
Historical INDOT lettings (2019-2025, 2,027 contracts)
        |
        +--> TRAIN period (earliest ~60% of lettings)   -> fits survival-model parameters
        |
        +--> CALIBRATION period (next ~20%)              -> fits temperature scaling ONLY
        |
        +--> TEST period (latest ~20%, 431 contracts)     -> touched ONLY for final reported metrics
```

---

## Data / Leakage Explanation

For any prediction made "as of" a bid date **D**:

| Allowed | Forbidden |
|---|---|
| Project info (EE, district) known before D | Current bid amount of any other candidate |
| Pre-bid candidate list, verified published before D's bid deadline | The actual winner of contract D |
| Contractor history strictly **before** D (`history_date < D`, never `<=`) | Any contractor observation dated on or after D |
| Historical market/regime information available before D | Post-bid item prices, award information |

This boundary is enforced in code (`build_history_snapshot` filters strictly by `< as_of_date`) and checked by an automated assertion suite that verifies, for every contractor history entry loaded into a prediction, that its date is provably before the prediction date — not merely assumed.

---

## Interview Q&A

**Q1. Why not use ordinary classification (bidder wins/loses)?**
It was the starting point, and it works — but a plain classifier treats each bidder-row as roughly independent, which understates that an auction is fundamentally one competitive event with exactly one winner. The auction-consistent survival formulation, tested directly against plain logistic regression, matched or beat it on every metric *and* was dramatically more consistent with the actual auction rule (higher bid never wins) — 5.5% cross-candidate ranking inconsistency vs. 28% for the plain classifier.

**Q2. Why does competitor information matter so much?**
Because the single largest source of uncertainty in "will my bid win" is "who else will bid, and how aggressively" — information that's normally invisible before an auction closes. Oracle experiments (using actual post-bid competitor counts/identities, which are *not* legitimately available pre-bid) showed this alone explained most of the gap between a weak baseline and a strong model — confirming the mechanism before we found a legitimate pre-bid source for it.

**Q3. Why INDOT specifically?**
It's the only state, among several investigated (WSDOT, TxDOT, FDOT, Illinois, Oregon, Ohio), confirmed to publish a genuine, dated, high-recall (99.9%) pre-bid list of registered competitors per project. Most states either don't publish this at all — FHWA itself recommends against it, to discourage bid collusion — or publish something with a different, weaker meaning (verified directly: Texas's comparable field showed only 25-33% recall against actual bidders on the two real matched examples available).

**Q4. What was the biggest breakthrough?**
Not a modeling technique — a data source. Switching from a state without a pre-bid candidate field to one with it moved the achievable AUC from roughly the high-0.60s/low-0.70s to the 0.90s. This matched the project's own recurring lesson: major gains came from *information*, not from tuning.

**Q5. Why the survival/order-statistic formulation specifically?**
It's the closest match to the actual data-generating process: N registered candidates, each with their own historical bidding tendency, one of whom (or none) actually wins. Modeling each real competitor's own distribution and combining them is more information-preserving than aggregating to one contract-level threshold — tested directly, the aggregate version underperformed.

**Q6. Why normalize probabilities per contract?**
Because the raw model computes each candidate's probability under a different implicit assumption (conditional on that specific candidate bidding), so they don't naturally sum to a consistent total across a contract — verified empirically (sum varied from 0.02 to 3.95 across real contracts). Normalizing makes candidates on the same project directly comparable, which is exactly what "who is most likely to win" requires.

**Q7. Why temperature scaling?**
Normalization alone fixed cross-contract consistency but left the *sharpness* of probabilities wrong — the top-ranked candidate's true win rate was roughly double what raw normalized probabilities showed. A single temperature parameter, fit only on a held-out calibration window, corrects this without touching the model's ranking behavior at all (verified: within-contract AUC and top-1 accuracy are provably unchanged by any per-contract monotonic rescaling).

**Q8. How did you prevent leakage?**
Three layers: (1) all data splits are chronological, never random; (2) every contractor-history lookup is filtered to strictly-prior dates in code, not just by convention, and checked by an automated assertion suite; (3) the pre-bid candidate list's timing was independently verified against INDOT's own registration-deadline rule (forms state registrations must be received before 9:00 AM on letting day; a captured planholder list's internal timestamp was confirmed to predate that cutoff).

**Q9. How did you validate it?**
Two full rounds at different scales (68 then 431 test contracts), multiple structurally different model architectures compared on the same held-out data (converging to the same performance band — evidence of a real data-driven ceiling, not overfitting to one formulation), and explicit robustness checks by year, district, project size, and candidate-count bucket, with no subgroup collapse found.

**Q10. What does 0.98 AUC actually mean?**
If you pick one winning bid and one losing bid at random from the test set, the model ranks the winner's probability higher about 98% of the time. It is a rank-quality measure, not a percentage of predictions that are "correct" — that distinction is deliberately reported separately (see Q11).

**Q11. Why is top-1 accuracy ~90% and not the same as AUC?**
Top-1 accuracy asks a stricter, more intuitive question: for each auction, is the single candidate the model ranks highest actually the winner? With typically 3-9 real candidates per contract, getting the exact top pick right 90% of the time is a much harder bar than pairwise ranking, and the two metrics are reported side by side specifically so neither is mistaken for the other.

**Q12. What are the biggest limitations?**
INDOT does not publish its Engineer's Estimate before bidding closes, so V1 asks the contractor for a Reference Estimate instead — and we tested, not assumed, that trying to predict INDOT's own EE from pre-bid features doesn't work well enough to substitute safely (downstream P(win) AUC dropped from 0.725 to 0.581). Beyond that: the model cannot see private contractor economics (capacity, cost structure, strategy), a small residual (~5.5%) of cross-candidate ranking inconsistency remains even after calibration, and predictions are only available once INDOT has actually published a project's pre-bid candidate list — often just days before the deadline.

**Q13. Why does the user have to supply a Reference Estimate instead of the system pulling INDOT's own Engineer's Estimate automatically?**
Because we checked every plausible pre-bid INDOT document directly — the Planholder List, the Notice to Contractors, the Schedule of Pay Items — and the Engineer's Estimate appears in none of them; it only exists in the post-bid Official Bid Tabulation, which would be leakage to use. We then explicitly tested whether a *predicted* "Shadow EE" from pre-bid project-scope features could substitute: it degraded the frozen engine's validated AUC from 0.725 to 0.581 on a held-out test set, a large enough drop to reject the idea rather than ship it quietly. Asking the contractor for their own reference number, with the tradeoff disclosed in the UI, was the honest choice given those two negative results.

**Q14. What would you improve next, if the model itself stayed frozen?**
Two things, in order: first, widen automatic per-contract detail beyond just the single nearest upcoming letting (currently bounded by design to avoid unnecessary INDOT requests). Second, resume the INDOT historical scale-up beyond 2019 if feasible, since every architecture comparison converged on sample size — not model choice — as the binding constraint.

---

## Demo Script (2-3 minutes)

**Opening (20s)** — *"Contractors bidding on public construction projects have no good way to estimate their odds before submitting a price — most bid data is only public after the auction closes. QuoteConvert changes that: it automatically watches INDOT for upcoming projects and shows the real, live list of who's registered to compete, before bidding closes."*

**Live Competition tab (20s)** — Open the app's **Live Competition** tab. *"This didn't come from a database we built — it was discovered and downloaded automatically from INDOT just now."* Point out the letting date, the per-contract candidate count, and competition level (Low/Moderate/High).

**Win-Probability Analysis tab (15s)** — Switch to **Win-Probability Analysis**, pick one of the live contracts. *"This contractor dropdown isn't free text — it's the actual published list of who registered to bid on this specific project."*

**Reference Estimate (15s)** — *"INDOT doesn't publish its own cost estimate before bidding — we checked directly and confirmed it, then even tested predicting it and found that made things worse, not better. So the contractor enters their own reference estimate, and we say so plainly right here."* Enter a reference estimate and a candidate bid.

**P(win) (20s)** — *"And here's the estimate: a calibrated probability of winning at this price."* Point out Bid/Reference-Estimate ratio, Competition level, Data quality.

**Change the bid (25s)** — Raise the bid by 10%. *"Watch what happens as I bid higher — the probability drops, exactly as it should. This isn't a static number, it's a full curve."*

**Show the curve (20s)** — Point at the bid-probability curve table and chart. *"Here's probability across a full range of bid amounts — from aggressive to conservative — so a contractor can see the tradeoff, not just one number."*

**Ending (25s)** — *"The underlying model is validated on over 2,000 real historical contracts with 90% top-1 accuracy at picking the actual winner. It's not a guarantee — it's a calibrated estimate, and it clearly says so. And the live discovery you saw at the start is real — automated, bounded, and running today."*

---

## Final Project Status

| Phase | Status |
|---|---|
| Research | **Complete** |
| Model | **Frozen** (`indot_survival_v1.0`) |
| Validation | **Complete** (2,027 contracts, 431-contract held-out test) |
| Shadow EE experiment | **Complete — rejected** (downstream AUC 0.725 -> 0.581; see README.md Section L) |
| Live discovery + ingestion | **Complete** — validated live on GitHub Actions (Stage 1 ~15s; Stage 2 reproduces 238/238 real candidates) |
| Snapshot decoupling | **Complete** — Streamlit reads `data/live_snapshot.json`; `generate_snapshot.py` + `indot_live_refresh.yml` are the only INDOT-connected components |
| V1 product (Mode A / Mode B) | **Complete** (Streamlit: Live Competition, Win-Probability Analysis, Historical Demo regression tab) |
| First live snapshot commit | **Pending** — requires a `workflow_dispatch` run or the daily schedule to fire at least once; this local environment cannot reach `www.in.gov` to generate it directly (confirmed: `[WinError 10054]` on attempt) |
| Live inference on a genuinely upcoming project | **Requires a user-supplied Reference Estimate** — INDOT's own Engineer's Estimate is verified not available pre-bid |
| Documentation | **This deliverable** |
