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
