# Build Paper 3 (ACM sigconf, e-Energy 2027 target). Requires tectonic (or latexmk with pdflatex and
# bibtex); "make pages" also needs pdfinfo and pdftotext (poppler).
#   make pdf        # anonymized submission build: main.pdf (the default)
#   make camera     # de-anonymized camera-ready build: camera-ready.pdf
#   make both       # both builds
#   make pages      # page count and the page on which the references start, per build
#   make stats STATS=<analysis-out>/stats.json        # generated/stats.tex for the paper's keys (final mode)
#   make stats-check STATS=<analysis-out>/stats.json  # list keys the paper uses that stats.json lacks
#   make todo       # every TODO marker in the sources
#   make keys       # every statistics key the paper uses (record.tex keys marked)
#   make clean
# Draft mode: without generated/stats.tex every \stat key prints as a yellow TODO box, table cells
# as a red TODO, and every page carries a DRAFT -- NO RESULTS watermark.
#
# Final mode: tools/stats_json_to_tex.py reads analyze_paper3.py's OUT/stats.json, flattens it with the analysis's
# own outputs.flatten and writes one \defstat per key that main.tex uses (formatted by outputs.tex_number) to
# generated/stats.tex. It refuses if a key is missing or not a scalar. The file's presence switches the build to final
# mode, where every missing key is a hard error. Values from committed records that the analysis does not produce
# (amend.a7.*, campaign.<host>.*) go in the tracked, hand-kept record.tex of \defstat lines (macros.tex). A synthetic
# stats.json writes \syntheticstatstrue, which watermarks every page.
STATS ?=

PYTHON ?= python3
TEX := $(shell command -v tectonic >/dev/null 2>&1 && echo tectonic || echo latexmk)

# Fixed build time for a reproducible \today: 2026-11-23 12:00 UTC.
SOURCE_DATE_EPOCH ?= 1795435200
export SOURCE_DATE_EPOCH

.PHONY: pdf anon camera both stats stats-check pages todo keys clean

pdf: anon

anon:
ifeq ($(TEX),tectonic)
	tectonic -X compile --keep-logs main.tex
else
	latexmk -pdf -interaction=nonstopmode -halt-on-error main.tex
endif

camera:
ifeq ($(TEX),tectonic)
	tectonic -X compile --keep-logs camera-ready.tex
else
	latexmk -pdf -interaction=nonstopmode -halt-on-error camera-ready.tex
endif

both: anon camera

stats:
	@test -n "$(STATS)" -a -f "$(STATS)" || (echo "usage: make stats STATS=<analyze_paper3.py output dir>/stats.json" && exit 2)
	$(PYTHON) tools/stats_json_to_tex.py --stats "$(STATS)" --output generated/stats.tex

stats-check:
	@test -n "$(STATS)" -a -f "$(STATS)" || (echo "usage: make stats-check STATS=<analyze_paper3.py output dir>/stats.json" && exit 2)
	$(PYTHON) tools/stats_json_to_tex.py --stats "$(STATS)" --check

pages:
	@for f in main.pdf camera-ready.pdf; do \
		test -f $$f || continue; \
		n=$$(pdfinfo $$f | awk '/^Pages:/{print $$2}'); echo "$$f: $$n pages"; \
		for p in $$(seq 1 $$n); do \
			if pdftotext -f $$p -l $$p $$f - | grep -q -i '^references$$'; then \
				echo "  references start on page $$p"; break; fi; done; \
		for p in $$(seq 1 $$n); do \
			if pdftotext -f $$p -l $$p $$f - | grep -q -i '^hosts validated but not measured$$'; then \
				echo "  appendices start on page $$p"; break; fi; done; \
	done

todo:
	@grep -n -E 'TODO|\\pending\{|\\todoverify\{|\\todoplaceholder\{' main.tex macros.tex references.bib camera-ready.tex Makefile

keys:
	@$(PYTHON) tools/stats_json_to_tex.py --list-keys

clean:
	rm -f main.pdf camera-ready.pdf *.aux *.bbl *.blg *.log *.out *.xdv *.fls *.fdb_latexmk
