# Draft: request for TPC written permission (derived-work name use)

**To:** info@tpc.org
**From:** Daniel Ejiofor <ejiod@bu.edu>
**Subject:** Permission request: use of "TPC-H" to describe a derived workload in academic papers

---

Dear TPC,

I am a Master's student at Boston University. I am preparing two academic research papers and a
public reproducibility package that use a workload derived from TPC-H, and I am requesting the TPC's
written permission, under Section 8.1.4 of the TPC Policies, to use the name "TPC-H" to describe this
derived work.

**What the work is.** A single-host performance study comparing CPU and GPU dataframe/SQL engines
(Polars, Apache Spark, and their GPU-accelerated variants). Data are generated with the DuckDB 1.5.6
`tpch` extension (`dbgen`); the workload uses a selected, adapted subset of TPC-H queries plus custom
queries, with decimal columns normalized to double precision and our own timing and summary method.
It is not an audited TPC-H run, and no TPC-H primary or optional metrics (e.g., QphH, price/performance)
are reported.

**How the name is used.** Only as "derived from TPC-H" / "TPC-H-derived", always with the disclaimer:
"This workload is derived from the TPC-H Benchmark and as such is not comparable to published TPC-H
Benchmark results, as the results of this workload do not comply with the TPC-H Benchmark
Specification." The disclaimer appears in each paper's front matter, in figure and table captions, and
in the repository. Results are never compared with official TPC-H results.

**Where it will appear.**
1. Paper: "CPU Dataframes versus GPU Acceleration on a Single Host: A Pre-registered, Randomized
   Comparison of Polars and Spark on a TPC-H-Derived Workload, Replicated across GPU Generations"
   (planned: arXiv preprint; submission to PVLDB).
2. Paper: "What Is the GPU Speedup Measured Against? Library-Version Lock-In and Core-Count Confounds in
   CPU-versus-GPU Dataframe Benchmarks" (planned: arXiv preprint; submission to an ACM journal).
3. Public repository and data archive: https://github.com/ejiod2025/gpu-spark-polars-study and a
   Zenodo deposit. TPC software (dbgen) is not redistributed; data are generated at run time.

The work is non-commercial academic research, distributed without charge, with no marketing purpose.

I would be grateful for written permission, or for guidance on any wording or conditions the TPC
requires (including preferred disclaimer text or whether the name may appear in a title).

Thank you,
Daniel Ejiofor
Boston University
ejiod@bu.edu
