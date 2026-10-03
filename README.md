# Replication package: project-declared ignore revisions and line-based ownership

This package accompanies an article by Emre Can Yılmaz on project-declared ignore revisions and line-based ownership, submitted to Empirical Software Engineering.

The study uses a census of 435 GitHub repositories with at least 5,000 stars. It measures how honoring a repository's `.git-blame-ignore-revs` file changes `git blame` attribution and line-based ownership measures.

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

## Privacy

Developer identities are normalized with each repository's `.mailmap` and replaced by SHA-256 hashes computed together with the repository name, and file paths are stored as hashes. Commit hashes and commit subject lines of the public repositories are kept, because they identify the declared commits.

## Requirements

- Python 3.13 with the packages in `requirements.txt` (`pip install -r requirements.txt`).
- For re-running the measurements only: Git 2.55.0, the GitHub CLI `gh` logged in, and about 150 GB of disk space for the clones. The scripts call Git at `/opt/homebrew/bin/git`; change the path in `scripts/measure.py` and `scripts/measurement_contract.py` if needed.

## Reproduce the results

To recompute every number, table, and figure from the stored measurements, run:

```
./run_all.sh
```

The run takes a few minutes and writes `output/numbers.tex`, `output/tables/`, and `output/figures/`. The macro values in `output/numbers.tex` are the numbers reported in the article.

To rebuild the frame and re-run every measurement as well, run `./run_all.sh --measure`. This clones all repositories and takes days. GitHub repositories change over time, so a new run measures newer revisions than the stored records, which were collected on 1 October 2026.

## License

Code: MIT License (`LICENSE`). Data: Creative Commons Attribution 4.0 International (`LICENSE-DATA`).
