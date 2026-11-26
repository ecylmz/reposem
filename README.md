# Replication package: project-declared and learned blame ignore lists and line-based ownership

This package accompanies an article by Emre Can Yılmaz on project-declared ignore revisions and line-based ownership.

The study uses a census of 435 GitHub repositories with at least 5,000 stars. It measures how honoring a repository's `.git-blame-ignore-revs` file changes `git blame` attribution and line-based ownership measures (RQ1 to RQ4). Version 2.0.0 adds the folder `detector/`: a detector that is trained on the project declarations and recommends which commits blame should ignore, evaluated on held-out repositories (RQ5 and RQ6) and applied to 150 popular repositories that declare nothing (RQ7).

## Contents

| Path | Content |
|---|---|
| `scripts/build_frame.py` | Enumerates GitHub repositories with at least 5,000 stars, checks for a root `.git-blame-ignore-revs` file, fetches commit and contributor counts, and applies the inclusion criteria. |
| `scripts/measure.py` | Clones one repository and measures the touched, random, and historical file samples under the four blame lanes. |
| `scripts/supplement.py` | Line-level heuristic comparison, survival of declared and matched commits, and the inputs of the later-maintenance baselines. |
| `scripts/bulk.py` | Later-commit counts without commits that touch more than 20 files. |
| `scripts/variants.py` | Reruns the declared lane with only formatter and whitespace commits, and with approximate mappings treated as unblamable. |
| `scripts/extra_lanes.py` | Measures touched files above 5,000 lines, and the `-w -M -C -C -C` lane on a subsample. |
| `scripts/github_check.py` | Compares the declared lane with the blame that the GitHub GraphQL API reports. |
| `scripts/declarations_check.py`, `scripts/unresolved.py`, `scripts/mechanism.py` | Describe the ignore files, classify inactive entries, and characterize owner changes. These steps need the clones or the GitHub API. |
| `scripts/analyze_*.py`, `scripts/kappa.py` | Compute every estimate, interval, and test reported in the article. |
| `scripts/make_numbers.py`, `scripts/figures.py` | Write the numbers, tables, and figures of the article. |
| `scripts/measurement_contract.py` | Git helpers: pinned Git runner, `.mailmap` identity normalization, ignore-file parsing. |
| `data/frame_*.json*`, `data/frame.json`, `data/study_repos.txt` | The sampling frame, the presence check, repository metadata, and the 435 included repositories. |
| `data/study/`, `data/supplement/`, `data/bulk/`, `data/variants/`, `data/extra/`, `data/github_check/` | One JSON measurement record per repository and analysis. |
| `data/analysis/` | All analysis outputs, including the intermediate files of the steps that need clones. |
| `data/category_*.json` | The development and validation samples of the commit-category rule. |
| `rater/` | The blinded rating items, the answer key, the rating form, and the independent rater's labels. |

## Detector study (`detector/`)

| Path | Content |
|---|---|
| `detector/protocol.md`, `detector/decisions.md` | The study protocol, written before the experiments, and every later decision, deviation, run note, and checkpoint. The final data stayed closed until the tag `analysis-freeze`. |
| `detector/configs/detector.json` | Paths, seeds, split fraction, flag rate, number of folds, and bootstrap resamples. |
| `detector/src/` | Commit features (`common.py`, `features.py`), the labeled and unlabeled pools and the repository split (`pools.py`), the size-matched unlabeled commits (`matched_pool.py`), model selection on development repositories (`develop_matched.py`), leakage checks (`leakage.py`), the blame lanes with the detected ignore list (`lanes.py`), the RQ7 sample (`rq7_frame.py`), the frozen analysis (`analyze.py`), and the macro file writer (`paper_numbers.py`). `develop.py` is the first design that decision D-005 replaced. |
| `detector/results/<run>/` | One folder per recorded run. Each holds `run.json` (commit, command, times, exit status, input checksums) and `run.log`. |
| `detector/results/analysis-final/` | Per-repository tables and `analysis.json` of the frozen analysis on the final repositories. |
| `detector/results/numbers.json`, `detector/paper/numbers.tex` | The values that the article reports for RQ5 to RQ7. |

The planned analyses are RQ5 (detection on 162 held-out repositories, primary measure size-matched PU-AUC), RQ6 (recovery and reassignment of the detected list against `-w` and `-w -M -C` on the same repositories), and the two ablations. RQ7 is descriptive. Every other analysis is exploratory.

To rebuild the RQ5 to RQ7 numbers from the deposited result files, run `make numbers` in `detector/` (minutes; needs only the files in this package). `make reproduce` reruns the blame lanes of the final evaluation. It needs the clones of the 162 final and 150 RQ7 repositories (about 140 GB) and takes several hours.

## Privacy

Developer identities are normalized with each repository's `.mailmap` and replaced by SHA-256 hashes computed together with the repository name, and file paths are stored as hashes. Commit hashes and commit subject lines of the public repositories are kept, because they identify the declared commits.

## Requirements

- Python 3.13 with the packages in `requirements.txt` (`pip install -r requirements.txt`).
- For re-running the measurements only: Git 2.55.0, the GitHub CLI `gh` logged in, and about 150 GB of disk space for the clones. The scripts call Git at `/opt/homebrew/bin/git`; change the path in `scripts/measure.py` and `scripts/measurement_contract.py` if needed.

## Reproduce the results

For RQ1 to RQ4, to recompute every number, table, and figure from the stored measurements, run:

```
./run_all.sh
```

The run takes a few minutes and writes `output/numbers.tex`, `output/tables/`, and `output/figures/`. The macro values in `output/numbers.tex` are the numbers reported in the article.

To rebuild the frame and re-run every measurement as well, run `./run_all.sh --measure`. This clones all repositories and takes days. GitHub repositories change over time, so a new run measures newer revisions than the stored records, which were collected on 1 October 2026.

## License

The package supports derived replay: it regenerates every number, table, and figure from the deposited measurement records. The measurement scripts are included for end-to-end reruns, but repositories change over time.

Code: MIT License (`LICENSE`). Data: Creative Commons Attribution 4.0 International (`LICENSE-DATA`).
