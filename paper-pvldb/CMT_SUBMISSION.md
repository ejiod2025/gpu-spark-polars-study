# PVLDB Vol. 20 — CMT submission sheet (Paper 1, EA&B version)

Submission site: https://cmt3.research.microsoft.com/PVLDBv20_2027/
Cycle rule (official page, checked 2026-11-11): CMT opens on the 20th, abstract by the 25th, paper due on
the 1st at 5:00 PM Pacific. No extensions. Earliest: open 2026-11-20, abstract 2026-11-25, paper 2026-12-01.
The **author** submits and makes every attestation; assistants only prepare this sheet.

| Field | Value |
|---|---|
| Track | Experiment, Analysis & Benchmark (EA&B) |
| Title | CPU Dataframes versus GPU Acceleration on a Single Host: A Pre-registered, Randomized Comparison of Polars and Spark on a TPC-H-Derived Workload, Replicated across GPU Generations |
| Title tag (in the PDF) | [Experiment, Analysis & Benchmark] — already on page 1 |
| Author | Daniel Ejiofor, Boston University, Boston, MA, USA, ejiod@bu.edu (single-blind: name on page 1) |
| PDF | `paper-pvldb/main.pdf` — rebuild with `make -C paper-pvldb final` right before upload; 12 pages + references (refs start p. 11) |
| Artifact / reproducibility URL | https://github.com/ejiod2025/gpu-spark-polars-study (instructions: `REPRODUCE.md`; raw data DOI 10.5281/zenodo.24125306 — publish the Zenodo record before submitting, or the DOI will not resolve for reviewers) |
| Keywords | GPU acceleration; dataframes; Polars; Apache Spark; cuDF; RAPIDS; benchmarking methodology; pre-registration; query processing; performance evaluation |
| Subject areas | choose in CMT; suggested: Experiments & Benchmarks; Query Processing & Optimization; Hardware-aware data management / Modern hardware |
| Conflicts of interest | AUTHOR: BU advisors, co-authors in the last 2 years, close collaborators |
| Reviewer nomination | AUTHOR: PVLDB requires one author as reviewer candidate; with no prior SIGMOD/VLDB/ICDE/EDBT/CIDR papers, nominate yourself as best-qualified. Non-response lowers the yearly submission cap. |
| Disclosures in the paper | Exploratory pilot (Oct 26–28) and AI-tool use are stated in the paper; TPC-derived disclaimer on page 1 |

## Abstract (plain text, 229 words — paste into CMT)

We compare Polars and Spark, each on the CPU and on the GPU (both GPU paths use NVIDIA cuDF), on one NVIDIA H100 host. The workload is TPC-H-derived (six adapted TPC-H queries, SF1–SF300; six complex queries, SF30–SF300), not a TPC-H benchmark result. Its pre-registered design uses four rounds in a Williams Latin square, timing-independent integrity gates and 95% intervals across rounds. At SF300, GPU speedups over the same framework’s CPU engine were 2.81× (95% CI 2.64–2.99) for Polars and 2.82× (95% CI 2.77–2.88) for Spark on the baseline suite, and 4.70× (95% CI 4.47–4.94) and 4.02× (95% CI 3.77–4.28) on the complex suite. The best GPU engine was faster than Polars CPU from SF10 (baseline) and SF30 (complex), and slower at SF1. Polars GPU rejected 2 of the six complex queries as unsupported (CPU fallback disabled by design). However, the GPU engine requires Polars 1.44.x, and Polars 2.0.0 on the CPU was faster than 1.44.2 at every scale in both suites (all 95% CIs below 1; time ratio 0.73 to 0.92 on the baseline suite and 0.51 to 0.52 on the complex suite). Campaigns on A100, GH200 and B200 showed the same pattern, with GPU time ratios to the H100 of 1.34 to 1.71 on the A100 (slower) and 0.86 to 1.23 on the GH200 and B200. Across 4 hosts, 0 of 8,832 output comparisons disagreed with Polars CPU.
