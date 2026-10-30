# Build the paper. Requires tectonic or latexmk (pdflatex + bibtex).
#   make pdf                                              # draft if generated/stats.tex is absent
# Every additional ladder host (amendments A2, A3) needs a stated outcome, either its stats file
# or a not-run reason:
#   make stats STATS=<h100>/stats.json A100_STATS=<a100>/stats.json B200_STATS=<b200>/stats.json \
#              GH200_NOT_RUN="reason" XHOST=<cross_host-dir>
# XHOST is analyze_campaign.py's cross-host output; xhost_<HOST>.json is read for each host that ran.
#   make figures PLOTS=<h100-plots> A100=ran GH200=not-run B200=ran XHOST_PLOTS=<ladder-plots>
#   make keys                                             # every stats key the paper uses
# stats and figures fail on any missing key, figure or undecided host.

PYTHON ?= python3
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

.PHONY: pdf stats figures final keys clean

pdf:
ifeq ($(TEX),tectonic)
	tectonic -X compile main.tex
else
	latexmk -pdf -interaction=nonstopmode -halt-on-error main.tex
endif

stats:
	@test -n "$(STATS)" || (echo "usage: make stats STATS=<h100>/stats.json {A100,GH200,B200}_STATS=... or {A100,GH200,B200}_NOT_RUN=reason [XHOST=<cross_host-dir>]" && exit 2)
	$(PYTHON) tools/stats_to_tex.py $(STATS_ARGS)

figures:
	@test -n "$(PLOTS)" -a -n "$(A100)" -a -n "$(GH200)" -a -n "$(B200)" || (echo "usage: make figures PLOTS=<h100-plots> A100=ran|not-run GH200=ran|not-run B200=ran|not-run [XHOST_PLOTS=<dir>]" && exit 2)
	$(PYTHON) tools/sync_figures.py --plots $(PLOTS) --host a100=$(A100) --host gh200=$(GH200) --host b200=$(B200) \
		$(if $(XHOST_PLOTS),--cross-host-plots $(XHOST_PLOTS))

final: stats figures pdf

keys:
	@$(PYTHON) tools/stats_to_tex.py --list-keys

clean:
	rm -f main.pdf main.aux main.bbl main.blg main.log main.out main.fls main.fdb_latexmk
