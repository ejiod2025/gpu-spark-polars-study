# Citations not yet in references.bib (Paper 2)

Everything in `references.bib` is verified. Entries copied from `../paper/references.bib` keep
that file's verification. The entries added for Paper 2 were checked on 2026-10-30: DOIs
against the Crossref API record, PyPI entries against the PyPI JSON API, and the Lee et al. and
Shanbhag et al. paraphrases against their abstracts (OpenAlex and Semantic Scholar). The items
below are deliberately absent until someone verifies them.

| Candidate | Why it would help | Status |
|---|---|---|
| cudf-polars upstream commit `e0fb724` ("Pin Polars to 2.0 and drop Polars 1.x support", 2026-10-20) and RAPIDS 26.12 schedule | Shows that the lock-in is temporary and when it ends (Discussion, "Lock-in is structural") | Noted in `docs/POLARS_GPU_COMPAT.md` §2; not fetched for this paper. Cite the GitHub commit URL once checked |
| Provider documentation stating that a vCPU is one hardware thread (Lambda, or a general cloud-provider statement) | Supports reading guest-reported SMT as real sibling threads (§3.4, threats) | Not located for Lambda; do not cite AWS documentation for Lambda instances |
| Empirical studies of SMT effects on database or analytical workloads | Would let the Discussion say what SMT typically does to such engines, which this study did not measure | Literature search not done; the paper currently makes no such claim |
| Bailey, "Twelve Ways to Fool the Masses When Giving Performance Results on Parallel Computers" (Supercomputing Review, 1991) | Classic reference on unfair baselines | No DOI; bibliographic details not verified |
| Further CPU-vs-GPU "debunking" or fair-baseline studies for dataframes (e.g. independent Polars GPU or cuDF evaluations) | Related work depth | Not searched; Paper 1's TODO has the same gap |
| The earlier A100 study (this project) | §4.3 case study | Not public; cite once a stable URL or report exists (same as Paper 1's TODO) |
| Paper 1 as a real citation | `ejiofor2026companion` is "in preparation" | Replace with an arXiv id once posted |
| Intel Xeon 8480+/8592+, AMD EPYC 7J13 and NVIDIA Grace (Neoverse V2) product specifications | Only needed if the text quotes core counts, cache sizes or memory bandwidth of the physical parts | Not fetched. The paper uses only guest-reported values |
