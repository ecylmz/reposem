# Detector study (RQ5 to RQ7). Run from this folder.
#   make numbers    rebuild analysis.json, numbers.json, and numbers.tex from the deposited result files
#   make reproduce  rerun the final evaluation (needs the clones; hours)
PY ?= .venv/bin/python
FEAT = results/features-2/features.csv
MFEAT = results/matched-features/features.csv
SPLIT = results/pools/split.csv
MODEL = results/develop-matched-2
OUT = results/analysis-final

numbers:
	cd src && ../$(PY) analyze.py --role final --features ../$(FEAT) --matched-features ../$(MFEAT) --split ../$(SPLIT) \
	  --models ../$(MODEL) --rq6 ../results/rq6-final --rq7 ../results/rq7-final --out ../$(OUT) > /dev/null
	$(PY) src/paper_numbers.py $(OUT)/analysis.json results/numbers.json
	python3 tools/make-numbers.py results/numbers.json paper/numbers.tex

reproduce:
	grep ',final$$' $(SPLIT) | cut -d, -f1 > results/final_repos.txt
	cd src && ../$(PY) lanes.py --mode rq6 --repos ../results/final_repos.txt --model ../$(MODEL) --out ../results/rq6-final
	cd src && ../$(PY) lanes.py --mode rq7 --repos ../results/rq7-frame/rq7_repos.txt --model ../$(MODEL) --out ../results/rq7-final
	$(MAKE) numbers

.PHONY: numbers reproduce
