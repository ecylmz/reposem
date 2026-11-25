# Study protocol: detecting commits that blame should ignore

Status: draft, written on 2026-11-25. When the user approves this file, the date and the tag `phase/protocol` go here. After that date, every change goes through `decisions.md`.

Source: the conversation of 2026-11-25. The user asked for a new technique that gives the study a concrete "so what" contribution, without any human validation step. This protocol extends the published study (Zenodo v1.0.0, RQ1 to RQ4). It does not change RQ1 to RQ4.

Prior exposure: RQ1 to RQ4 results are known for all 406 repositories with an effective declaration. This includes the category shares of the 5,077 declared commits and their sizes. No result of a detector exists. The category knowledge can shape the features. Decision D-001 records this.

## Goal and paper type

- Problem (decided): only 3.9% of popular repositories declare the commits that blame must skip. Tools and studies have no correction for the other 96%.
- Paper type (decided): empirical study extended with a technique. The technique is a detector that recommends an ignore list for a repository without one.
- Target venue (decided by the user): Science of Computer Programming, research paper. LaTeX source accepted, no page limit. Rules are in `../../reposem/submission/scp/rules/`.
- Contribution type (proposed): a technique with an evaluation, plus the existing census. Of the 20 most recent SCP research papers, 9 present a technique or tool with an evaluation.
- Nearest prior work (proposed): DiffCat detects non-essential changes in Java (Kawrykow and Robillard, 2011). AG-SZZ skips cosmetic lines (Kim et al., 2006). Refactoring detectors find refactorings (Tsantalis et al., 2018). This detector differs in four ways. It works on commits in any language. It learns from labels that projects wrote themselves. Its output is an ignore list for blame. Its evaluation measures the effect on attribution, not only on classification.
- Literature base (decided): 63 peer-reviewed references now. The target stays at 50 or more.

## Research questions

RQ5: Detection. How well does a detector trained on project declarations recover the declared commits of repositories that it has not seen?

- Motivation: to be useful, a detector must find the commits that projects themselves choose to declare.
- Expected direction: the detector ranks declared commits above other commits better than every baseline.
- Rule: on the final repositories, the detector has a higher per-repository PU-AUC than the best baseline. The median paired difference is at least 0.05. A two-sided Wilcoxon signed-rank test gives a Holm-adjusted p below 0.05. Recall at the frozen threshold is also reported.
- Negative result: the declared commits are not separable with diff features, or simple rules separate them as well. The paper reports either case.
- Unit: repository. Measure: PU-AUC and recall at the frozen flag rate. Comparators: keyword rule, whitespace-only rule, random scores. Effect size: matched-pairs rank-biserial correlation.

RQ6: Attribution. Does blame with the detected ignore list reproduce the declared attribution better than Git's heuristics?

- Motivation: RQ3 showed that `-w` and `-w -M -C` recover only a third of the declared re-attributions. The detector must do better to be worth using.
- Expected direction: higher recovery than `-w` and `-w -M -C`, and less reassignment of undeclared lines than `-w -M -C`.
- Rule: on the final repositories, the median paired difference in recovery (detected minus each heuristic) is at least 0.10. The Holm-adjusted Wilcoxon p is below 0.05. The same rule with a margin of 0.02 applies to reassignment, in the direction of less reassignment.
- Negative result: a learned ignore list is not better than Git's options. The paper then recommends the declaration only.
- Unit: repository. Measures: recovery and reassignment as in RQ3, owner change and share distance against the declared lane. Comparators: the W and WMC lanes. Effect size: matched-pairs rank-biserial correlation.

RQ7: Reach. How much does line-based ownership change under the detected list in popular repositories that declare nothing?

- Motivation: this answers the objection that the problem concerns only 3.9% of repositories.
- Exploratory and descriptive. No direction is assumed.
- Estimate: for each repository, the share of randomly drawn files whose owner changes under the detected list. The estimate is the mean of these shares across repositories. A repository bootstrap gives the 95% interval.
- Negative result: if the change is near zero, the problem is limited to repositories that declare commits.
- Unit: repository. The detector has no ground truth there. The paper labels the result as an estimate under the detector.

## Subjects and data

- RQ5 and RQ6: the 406 repositories with an effective declaration, from the existing census (frame date 2026-10-01). The analyzed revisions are the ones of the census.
- RQ7: a random sample of 150 repositories from the same frame that keep no root ignore file. The same inclusion criteria apply: at least 200 commits, at least 10 mentionable users, and at most 2.5 GB. The seed is in `configs/`. The frame file of v1.0.0 lists these repositories.
- Commit pool per repository (proposed): all declared commits that resolve on the default branch are positives. Up to 400 other non-merge commits are drawn uniformly from the history up to the analyzed revision. These are unlabeled.
- Source and terms: public GitHub repositories, cloned with Git. Existing clones are reused. The GitHub terms allow this use.
- Ethics: no human participants. Author identities are hashed per repository, as in v1.0.0.

## Labels and measures

- Label: if the repository's ignore file declares a commit, the commit is positive. Projects do not declare every mechanical commit. Unlabeled commits are therefore a mix of mechanical and other commits.
- This is positive-unlabeled (PU) data. PU-AUC is a lower bound of the true AUC under the usual assumption that declared commits are a random subset of mechanical commits. The paper states this assumption as a threat.
- No human labels are used at any stage (decided by the user).
- Features: computed from the diff of each commit, with no file-type knowledge.
  - Content preservation after removing whitespace, per changed line pair.
  - Token multiset preservation between deleted and added lines.
  - Letters-and-digits sequence equality per file.
  - Size: changed files, changed lines, and their ratio.
  - Change to a formatter or linter configuration file in the same commit.
  - Message keywords, as one feature group.
  - Bot author.

## Approaches and baselines

- Detector: a classifier on the features above. Candidates are logistic regression and gradient boosting. The choice happens on development data only.
- Keyword rule: the commit message matches the formatter, style, lint, or whitespace keywords of the RQ1 category rule.
- Whitespace-only rule: `git diff -w` of the commit is empty.
- Random scores: a lower bound for PU-AUC.
- Ablations: the detector without message keywords, and the detector without content-preservation features.
- For RQ6, the W and WMC lanes of v1.0.0 are the comparators. They are recomputed on the same files for the final repositories.

## Validation and final evaluation data

- Split key: repository. A seeded random split assigns 60% of the 406 repositories to development and 40% to final evaluation.
- Development: model choice, features, and the threshold. Tuning uses five folds grouped by repository.
- Final evaluation: closed until the freeze. Only counts and overlap checks run on it before the freeze.
- Threshold: the score above which the detector flags a commit. It is set on development data so that at most 1% of unlabeled commits are flagged.
- Leakage rules: features use only the commit and its parent. Nothing reads the ignore file or the commit's later history.
- RQ7 uses the detector frozen on development data. It is not refit on final data.

## Analysis plan

- Units: repositories. RQ5 and RQ6 have about 162 final repositories. RQ7 has 150.
- Tests: two-sided Wilcoxon signed-rank tests on per-repository values, with zero differences discarded. The effect size is the matched-pairs rank-biserial correlation, reported with its sign.
- Families for Holm: RQ5 detector against three baselines. RQ6 detector against W and WMC, for recovery and for reassignment (four tests).
- Intervals: 95% percentile bootstrap over repositories, 5,000 resamples.
- Failed runs: a repository whose features or blame fail is reported and left out. Its count is reported.
- Planned analyses: RQ5, RQ6, RQ7 as above, and the two ablations. Every other analysis is exploratory.

## Planned checks for threats

- PU labels: report PU-AUC as a lower bound, and the share of flagged unlabeled commits per repository.
- Declared commits that are not mechanical: RQ1 found 14% in the category "other". A sensitivity analysis repeats RQ5 with formatter and whitespace positives only.
- RQ7 has no ground truth. The paper reports it as an estimate under the detector.

## Budget

- Feature extraction: about 165,000 commits, an estimated 1 to 2 hours on this Mac with 8 workers.
- RQ6 blame on final repositories: an estimated 3 to 5 hours.
- RQ7 cloning of 150 repositories: about 40 GB and 2 to 4 hours, then about 2 hours of blame.
- No money is spent. Each step allows one retry.

## Replication package

- A new version, v2.0.0, of the existing Zenodo record. It adds the detector code, the configurations, the result files, and the RQ7 frame. It contains no submission material.
- Reproducibility level: derived replay for all RQs. `make numbers` rebuilds every number from the stored results.

## Open questions

- Approval of RQ5 to RQ7, the rules, and the 60/40 split: the user.
- Approval of the compute budget above (about 10 hours, no money): the user.
