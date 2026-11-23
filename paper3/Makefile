# Build Paper 3 (ACM sigconf, e-Energy 2027 target). Requires tectonic (or latexmk with pdflatex and
# bibtex); "make pages" also needs pdfinfo and pdftotext (poppler).
#   make pdf        # anonymized submission build: main.pdf (the default)
#   make camera     # de-anonymized camera-ready build: camera-ready.pdf
#   make both       # both builds
#   make pages      # page count and the page on which the references start, per build
#   make stats STATS=<analysis-out>/stats.tex   # install analyze_paper3.py's stats.tex (final mode)
#   make todo       # every TODO marker in the sources
#   make keys       # every statistics key (or key prefix) the paper uses
#   make clean
# Draft mode: without generated/stats.tex every \stat key prints as a yellow TODO box, table cells
# as a red TODO, and every page carries a DRAFT -- NO RESULTS watermark.
#
# Final mode: analyze_paper3.py writes OUT/stats.tex (one \defstat per scalar statistic of its
# flattened stats.json). "make stats" copies it unchanged to generated/stats.tex; its presence switches
# the build to final mode, where every missing key is a hard error. Values from committed records that
# the analysis does not produce (amendment A6 date, commit and sha256; launch-record facts such as
# campaign.<host>.driver) go in a hand-kept generated/record.tex of \defstat lines (macros.tex).
# A stats file from a synthetic tree carries \syntheticstatstrue and watermarks every page.
STATS ?=

TEX := $(shell command -v tectonic >/dev/null 2>&1 && echo tectonic || echo latexmk)

# Fixed build time for a reproducible \today: 2026-11-23 12:00 UTC.
SOURCE_DATE_EPOCH ?= 1795435200
export SOURCE_DATE_EPOCH

.PHONY: pdf anon camera both stats pages todo keys clean

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
	@test -n "$(STATS)" -a -f "$(STATS)" || (echo "usage: make stats STATS=<analyze_paper3.py output dir>/stats.tex" && exit 2)
	mkdir -p generated
	cp "$(STATS)" generated/stats.tex
	@grep -q 'syntheticstatstrue' generated/stats.tex && echo "note: SYNTHETIC stats installed (pages are watermarked)" || true

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
	@grep -o -E '\\(stat|tstat|ratioci|ratiotext|tratioci|xcell)\{[^}]+\}' main.tex | sed -E 's/^\\([a-z]+)\{(.*)\}$$/\2  (\1)/' | sort -u

clean:
	rm -f main.pdf camera-ready.pdf *.aux *.bbl *.blg *.log *.out *.xdv *.fls *.fdb_latexmk
