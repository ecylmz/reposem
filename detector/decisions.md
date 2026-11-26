# Decisions

This file records the decisions, the deviations from `protocol.md`, and the checkpoints, in time order. Never edit an old entry. To correct an entry, add a new one that names it.

## Entries

## D-001, 2026-11-25: prior exposure

Type: prior exposure
Phase: protocol
Seen results: RQ1 to RQ4 of v1.0.0 for all 406 repositories, including the category shares and sizes of the 5,077 declared commits.
Change: none.
Reason: the feature design knows that formatter runs dominate the declarations and that declared commits are large. This knowledge covers the repositories that the split later assigns to final evaluation.
Decided by: recorded by Claude.
Effect: the features are fixed before the split and do not use any detector result. The paper reports this exposure.
Commit: none yet.

## D-002, 2026-11-25: no human validation

Type: decision
Phase: protocol
Seen results: none for the detector.
Change: the first plan had a manual precision check of flagged commits outside the declarations. The protocol has no human step.
Reason: the user does not want human validation at any stage.
Decided by: the user.
Effect: precision outside the declarations is not measured. RQ5 reports PU-AUC as a lower bound. RQ7 is an estimate under the detector.
Commit: none yet.

## D-003, 2026-11-25: protocol checkpoint

Type: checkpoint
Phase: protocol
Seen results: none for the detector.
Change: none.
Reason: `protocol.md` is ready for approval.
Decided by: proposed by Claude.
Effect: approval pending. The open questions are the RQ rules, the 60/40 split, and the compute budget of about 10 hours with no money.
Commit: see the tag `phase/protocol`.

## D-004, 2026-11-25: protocol approved

Type: approval
Phase: protocol
Seen results: none for the detector.
Change: none.
Reason: the user approved D-003, the rules of RQ5 to RQ7, the 60/40 split, and the compute budget of about 10 hours and 40 GB, with no money.
Decided by: the user.
Effect: the protocol is fixed. Later changes need an entry.
Commit: tag `phase/protocol`.

2026-11-25 decision: keyword features and the keyword baseline read the commit subject only, as the RQ1 category rule does. No results seen.
2026-11-25 run note: results/features failed to start because macOS has no `timeout` binary. results/features-2 reran the same configuration without the wrapper (167,144 commits, 0 failed).
2026-11-25 data: pools hold 5,014 positives (63 declared commits are merges or roots and are left out) and 162,130 unlabeled commits. Split: 244 development and 162 final repositories.

## D-005, 2026-11-26: size baseline and size-matched evaluation for RQ5

Type: deviation
Phase: method
Seen results: development results of run results/develop (244 development repositories). Chosen model median per-repository PU-AUC 0.998 (out-of-fold). A size rule alone (log of changed files) gives 0.988, log of changed lines 0.964, keyword rule 0.932. No final result seen.
Change: the protocol had no size baseline and used PU-AUC against uniformly drawn unlabeled commits. The study now (1) adds two size baselines (changed lines, changed files); (2) makes size-matched PU-AUC the primary RQ5 measure: each declared commit is compared with up to 3 unlabeled commits of the same repository and the same size bin (floor of log2 of changed lines plus one); (3) adds these matched commits to the development training data and chooses the model by median size-matched PU-AUC. The uniform PU-AUC stays as a secondary measure. The threshold still flags 1% of the uniformly drawn unlabeled commits. The RQ5 Holm family now holds five baselines.
Reason: against uniformly drawn commits, commit size separates declared commits almost perfectly, so the planned measure hits its ceiling and cannot show what the detector adds.
Decided by: proposed by Claude, approved by the user.
Effect: RQ5 changes its primary measure and its comparators before the final data is opened. RQ6 and RQ7 do not change. The development run results/develop stays as the record of the first design.
Commit: see the next commit.

2026-11-26 data: RQ7 sample of 150 repositories drawn from 11,245 no-file candidates (209 checked, 45 below 200 commits, 14 below 10 mentionable users, 0 clone failures). Commit and contributor counts come from the GitHub API on 2026-11-26, not on the frame date; the analyzed revision is the default-branch revision of 2026-10-01.
2026-11-26 run note: results/develop-matched failed at import because src/numbers.py shadowed the standard library module numbers. The file is now src/paper_numbers.py. results/develop-matched-2 reruns the same configuration.

2026-11-26 run note: results/develop-matched-2 (D-005 design, 244 development repositories, 2,814 positives, 8,235 matched rows). The four HGB configurations tie at a median size-matched PU-AUC of 1.000. The rule takes the first maximum in grid order: HGB, learning rate 0.05, 15 leaf nodes. Threshold 0.897 (1% of uniform unlabeled commits, out-of-fold), out-of-fold recall at the threshold 0.506. Development medians of size-matched PU-AUC: keyword 0.900, changed files 0.751, changed lines 0.516, whitespace-only 0.500, random 0.482. Ablations: no keywords 0.978, no content 0.976, no size 0.999.

2026-11-26 run note: results/leakage. Label shuffle within repositories gives a median size-matched PU-AUC of 0.617 (uniform 0.820), against 1.000 for the true labels. The excess over 0.5 comes from repository composition: repositories with many declared commits also contribute many matched commits of the same size, so a shuffled model still learns repository-level size and style. Permutation importance ranks token-preserving diff, the lint and formatter keywords, alphanumeric equality, and whitespace-preserving diff first. Size features add almost nothing. Counts only: 30 commit ids occur in both development and final repositories, and 34 positive commit ids occur in more than one development repository (shared history).

## D-006, 2026-11-26: shared commits and the RQ5 rule in the analysis script

Type: deviation
Phase: method
Seen results: development results of results/develop-matched-2 and results/leakage. No final result seen; the overlap count reads commit ids only.
Change: (1) RQ5 on final repositories drops every commit id that also occurs in the development data (30 ids), because those commits were training data. (2) The RQ5 rule compares the detector with the best of the five baselines by median size-matched PU-AUC (D-005 extended the comparators; the margin 0.05 and the Holm-adjusted p below 0.05 stay). (3) RQ6 reassignment rule: the median paired difference (detected minus heuristic) is at most -0.02, against W and against WMC.
Reason: (1) removes a leak through shared history; (2) and (3) write down how the script applies the protocol rules.
Decided by: proposed by Claude; part of the freeze approval.
Effect: about 30 commits fewer in the final RQ5 data. No other change.
Commit: see the tag `analysis-freeze`.

## D-007, 2026-11-26: analysis freeze checkpoint

Type: checkpoint
Phase: freeze
Seen results: development results only (see the run notes and D-006).
Change: none.
Reason: code, configurations, seeds, model (results/develop-matched-2), threshold, and the analysis scripts are frozen. The pipeline ran end to end on development data (scratch): lanes in rq6 and rq7 mode on development repositories, analyze.py, paper_numbers.py, make-numbers.py.
Decided by: proposed by Claude.
Effect: approval pending. After approval, the final evaluation runs once: make reproduce (RQ6 lanes on 162 final repositories, RQ7 lanes on 150 repositories, then make numbers). Estimate: 2 to 4 hours of compute, no money, no new disk.
Commit: a678b75923d3d5856e07815a3c24cc3972ce749a (tag analysis-freeze).

## D-008, 2026-11-26: freeze approved

Type: approval
Phase: freeze
Seen results: development results only.
Change: none.
Reason: the user approved D-007, including D-006.
Decided by: the user.
Effect: the final evaluation runs once.
Commit: tag `analysis-freeze`.

2026-11-26 run note: final evaluation ran once (results/rq6-final: 162 of 162 repositories OK; results/rq7-final: 150 of 150 OK; results/analysis-final through `make numbers`). Known cosmetic issue found after the freeze: analyze.py reads every JSON file in a lanes folder, so the run-record file run.json enters the status count as one extra entry with status "ok". The analysis skips it because its status is not "OK", so no number changes. Not rerun.
2026-11-26 results (final, planned): RQ5 rule met (detector against the keyword rule, median size-matched PU-AUC 0.994 against 0.857, median difference 0.083, Holm p < 0.001; 30 shared commits dropped). RQ6 recovery rule met against W and WMC (mean recovery 0.811 against 0.305 and 0.323). RQ6 reassignment rule met against WMC (median difference -0.071) and not met against W (median difference -0.013, margin -0.02). RQ7 estimate: 1.4% of random files change owner under the detected list (95% CI 0.8 to 2.3).
