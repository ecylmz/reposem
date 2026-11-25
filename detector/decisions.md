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
