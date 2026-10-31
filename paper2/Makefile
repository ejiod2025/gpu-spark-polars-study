# Build Paper 2. Requires tectonic or latexmk (pdflatex + bibtex), and python3.
#   make final        # stats + figures + pdf against the analysis outputs below
#   make pdf          # draft if generated/stats.tex is absent (missing keys show as boxes)
#   make keys         # every stats key the paper uses, all cases
# Inputs default to the committed copies under ../results/campaign-20261030 (override any *_DIR).
# The Paper 2 analysis file (protocol check, factorization, adjusted E1, F20/F21) is optional.

PYTHON ?= python3
TEX := $(shell command -v tectonic >/dev/null 2>&1 && echo tectonic || echo latexmk)
# Defaults: the committed inputs. H100/GH200 are the Paper 1 record (analysis/); the A100/B200 finals,
# the four-host cross-host file and the Paper 2 analysis are in analysis-paper2/.
RESULTS ?= ../results/campaign-20261030
H100_DIR  ?= $(RESULTS)/analysis/h100-final
GH200_DIR ?= $(RESULTS)/analysis/gh200-final
A100_DIR  ?= $(RESULTS)/analysis-paper2/a100-final
B200_DIR  ?= $(RESULTS)/analysis-paper2/b200-final
XHOST_DIR ?= $(RESULTS)/analysis-paper2/xhost-final-v2
EXTRA_DIR ?= $(RESULTS)/analysis-paper2/paper2-final
EXTRA_JSON = $(wildcard $(EXTRA_DIR)/paper2.json)

STATS_ARGS = --stats $(H100_DIR)/stats.json \
	--stats a100=$(A100_DIR)/stats.json --stats gh200=$(GH200_DIR)/stats.json \
	--stats b200=$(B200_DIR)/stats.json \
	--stats xhost_a100=$(XHOST_DIR)/xhost_A100.json \
	--stats xhost_gh200=$(XHOST_DIR)/xhost_GH200.json \
	--stats xhost_b200=$(XHOST_DIR)/xhost_B200.json \
	$(if $(EXTRA_JSON),--extra $(EXTRA_JSON))

.PHONY: pdf stats figures final keys inputs clean

# Fixed build time for a byte-reproducible PDF (also sets \today): 2026-10-30 12:00 UTC.
SOURCE_DATE_EPOCH ?= 1793361600
export SOURCE_DATE_EPOCH

inputs:
	@echo "H100  $(H100_DIR)"; echo "A100  $(A100_DIR)"; echo "GH200 $(GH200_DIR)"; echo "B200  $(B200_DIR)"
	@echo "XHOST $(XHOST_DIR)"; echo "EXTRA $(if $(EXTRA_JSON),$(EXTRA_JSON),(none))"

pdf:
ifeq ($(TEX),tectonic)
	tectonic -X compile main.tex
else
	latexmk -pdf -interaction=nonstopmode -halt-on-error main.tex
endif

stats: inputs
	$(PYTHON) tools/stats_to_tex.py $(STATS_ARGS)

figures:
	$(PYTHON) tools/sync_figures.py --host h100=$(H100_DIR) --host a100=$(A100_DIR) \
		--host gh200=$(GH200_DIR) --host b200=$(B200_DIR) --xhost $(XHOST_DIR) \
		$(if $(wildcard $(EXTRA_DIR)),--extra $(EXTRA_DIR))

final: stats figures pdf

keys:
	@$(PYTHON) tools/stats_to_tex.py --list-keys

clean:
	rm -f main.pdf main.aux main.bbl main.blg main.log main.out main.fls main.fdb_latexmk
