#!/usr/bin/env bash
# Recompute every number, table, and figure of the article from the stored measurements.
#   ./run_all.sh            analysis only (minutes; uses data/ and the precomputed files in data/analysis/)
#   ./run_all.sh --measure  rebuild the frame, clone all repositories, and re-run every measurement (days, ~150 GB)
set -euo pipefail

cd "$(dirname "$0")"
py=${PYTHON:-python3}
analysis=data/analysis
mkdir -p "$analysis" output/tables output/figures

if [[ "${1:-}" == "--measure" ]]; then
  for step in enumerate check meta select; do
    "$py" scripts/build_frame.py "$step"
  done
  xargs -P 7 -I{} "$py" scripts/measure.py {} data/study < data/study_repos.txt
  xargs -P 7 -I{} "$py" scripts/supplement.py {} data/study data/supplement a,b,c < data/study_repos.txt
  xargs -P 3 -I{} "$py" scripts/bulk.py {} data/study data/bulk < data/study_repos.txt
  xargs -P 7 -I{} "$py" scripts/variants.py {} data/study data/variants < data/study_repos.txt
  xargs -P 5 -I{} "$py" scripts/extra_lanes.py {} data/study data/extra < data/study_repos.txt
  xargs -P 3 -I{} "$py" scripts/github_check.py {} data/study data/supplement data/github_check < data/study_repos.txt
  # Steps that need the clones or the GitHub API.
  "$py" scripts/declarations_check.py data/study "$analysis/declarations_check.csv"
  "$py" scripts/unresolved.py data/study "$analysis/unresolved.csv"
  "$py" scripts/mechanism.py data/study "$analysis/mechanism.csv"
fi

cp data/frame.json data/category_check.json "$analysis"/
"$py" scripts/analyze_full.py data/study "$analysis" > /dev/null
"$py" scripts/analyze_supplement.py data/study data/supplement "$analysis" > /dev/null
if [[ "${1:-}" == "--measure" ]]; then
  "$py" scripts/analyze_round3.py data/study data/supplement "$analysis" > /dev/null
fi
"$py" scripts/analyze_final.py data/study "$analysis" > /dev/null
"$py" scripts/kappa.py rater/answers rater/key.json "$analysis/kappa.json" > /dev/null
"$py" scripts/make_numbers.py "$analysis" output
"$py" scripts/figures.py "$analysis" output/figures
echo "Done: numbers in output/numbers.tex, tables in output/tables/, figures in output/figures/."
