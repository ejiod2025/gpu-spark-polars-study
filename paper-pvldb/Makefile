# Build the PVLDB Vol. 20 (EA&B) version of Paper 1. Requires tectonic (or latexmk with pdflatex
# and bibtex) and python3. Uses Paper 1's tools in ../paper/tools unchanged, pointed at this
# folder's main.tex and output paths.
#   make final        # stats + figures + pdf from the committed inputs (the defaults below)
#   make pdf          # draft if generated/stats.tex is absent (missing keys show as boxes)
#   make keys         # every stats key this paper uses
#   make check        # macros.tex is still a verbatim copy of ../paper/macros.tex
#   make pages        # page count and the page on which the references start
# Inputs and host outcomes have the same defaults and meaning as ../paper/Makefile.

CAMPAIGN ?= ../results/campaign-20261030/analysis
STATS ?= $(CAMPAIGN)/h100-final/stats.json
A100_STATS ?= $(CAMPAIGN)/a100-main/stats.json
GH200_STATS ?= $(CAMPAIGN)/gh200-final/stats.json
B200_STATS ?= $(CAMPAIGN)/b200-main/stats.json
XHOST ?= $(CAMPAIGN)/xhost-final
PLOTS ?= $(CAMPAIGN)/h100-final/figs
XHOST_PLOTS ?= $(CAMPAIGN)/xhost-final/figs
A100 ?= ran
GH200 ?= ran
B200 ?= ran

PYTHON ?= python3
TOOLS := ../paper/tools
TEX := $(shell command -v tectonic >/dev/null 2>&1 && echo tectonic || echo latexmk)

STATS_ARGS = --stats $(STATS) \
	$(if $(A100_STATS),--stats a100=$(A100_STATS)) \
	$(if $(GH200_STATS),--stats gh200=$(GH200_STATS)) \
	$(if $(B200_STATS),--stats b200=$(B200_STATS)) \
	$(if $(and $(XHOST),$(A100_STATS)),--stats xhost_a100=$(XHOST)/xhost_A100.json) \
	$(if $(and $(XHOST),$(GH200_STATS)),--stats xhost_gh200=$(XHOST)/xhost_GH200.json) \
	$(if $(and $(XHOST),$(B200_STATS)),--stats xhost_b200=$(XHOST)/xhost_B200.json) \
	$(if $(A100_NOT_RUN),--not-run "a100=$(A100_NOT_RUN)") \
	$(if $(GH200_NOT_RUN),--not-run "gh200=$(GH200_NOT_RUN)") \
	$(if $(B200_NOT_RUN),--not-run "b200=$(B200_NOT_RUN)")

.PHONY: pdf stats figures final keys check pages clean

# Fixed build time for a byte-reproducible PDF (also sets \today): 2026-10-30 12:00 UTC.
SOURCE_DATE_EPOCH ?= 1793361600
export SOURCE_DATE_EPOCH

pdf:
ifeq ($(TEX),tectonic)
	tectonic -X compile --keep-logs main.tex
else
	latexmk -pdf -interaction=nonstopmode -halt-on-error main.tex
endif

stats: check
	$(PYTHON) $(TOOLS)/stats_to_tex.py $(STATS_ARGS) --tex main.tex --output generated/stats.tex

figures:
	$(PYTHON) $(TOOLS)/sync_figures.py --plots $(PLOTS) --host a100=$(A100) --host gh200=$(GH200) \
		--host b200=$(B200) $(if $(XHOST_PLOTS),--cross-host-plots $(XHOST_PLOTS)) --output figures

final: stats figures pdf pages

keys:
	@$(PYTHON) $(TOOLS)/stats_to_tex.py --list-keys --tex main.tex

check:
	@cmp -s macros.tex ../paper/macros.tex || (echo "ERROR: macros.tex differs from ../paper/macros.tex" && exit 1)

# Prints the total page count and the first page holding the REFERENCES heading; the body
# (everything that counts towards the 12-page limit) is the pages before it, plus that page's
# part above the heading.
pages:
	@pdfinfo main.pdf | grep '^Pages:' || true
	@n=$$(pdfinfo main.pdf | awk '/^Pages:/{print $$2}'); for p in $$(seq 1 $$n); do \
		if pdftotext -f $$p -l $$p main.pdf - | grep -q '^REFERENCES$$'; then echo "References start on page $$p"; break; fi; done

clean:
	rm -f main.pdf main.aux main.bbl main.blg main.log main.out main.fls main.fdb_latexmk
