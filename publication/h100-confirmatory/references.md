# Primary references and their role

These references support methodological or implementation context. Experimental
facts and numerical findings require the archived evidence identified in
[claim-evidence-matrix.md](claim-evidence-matrix.md). Documentation was consulted
on 2026-10-26; the related-work synthesis rechecked the primary Kent repository,
IBM publication record, NIST handbook and Hesterberg preprint abstract on
2026-10-27. Installed pinned source and recorded effective settings govern
runtime claims where rolling documentation differs.

1. **Kalibera, T., and Jones, R. E. (2013).** Rigorous Benchmarking in Reasonable
   Time. *Proceedings of ISMM*, 63–74.
   [DOI](https://doi.org/10.1145/2464157.2464160);
   [author manuscript, University of Kent](https://kar.kent.ac.uk/33611/45/p63-kaliber.pdf).
   Supports separating repetition levels, reporting uncertainty, and allocating
   replication according to variation and experiment cost. It does not establish
   a universal repetition count or prove that this campaign's ten blocks have
   adequate power.

2. **Mytkowicz, T., Diwan, A., Hauswirth, M., and Sweeney, P. F. (2009).**
   Producing Wrong Data Without Doing Anything Obviously Wrong!
   *Proceedings of ASPLOS*.
   [Primary publication record, IBM Research](https://research.ibm.com/publications/producing-wrong-data-without-doing-anything-obviously-wrong).
   Supports caution about setup bias and experimental allocation. It does not
   demonstrate the absence of bias on this virtual host.

3. **NIST/SEMATECH.** e-Handbook of Statistical Methods, section 5.3.3.2,
   Randomized Block Designs.
   [Official handbook](https://www.itl.nist.gov/div898/handbook/pri/section3/pri332.htm).
   Supplies general blocking/randomization principles. The exact Williams
   sequences and balance checks are part of this study's frozen protocol and
   executable schedule tests.

4. **Holm, S. (1979).** A Simple Sequentially Rejective Multiple Test Procedure.
   *Scandinavian Journal of Statistics*, 6(2), 65–70.
   [Publisher archive record](https://www.jstor.org/stable/4615733).
   Source of the familywise multiple-testing procedure applied to the single
   prespecified 26-test family. This does not make the reported pointwise
   confidence intervals simultaneous or remove the assumptions behind input
   p values.

5. **Hesterberg, T. C. (2015).** What Teachers Should Know About the Bootstrap:
   Resampling in the Undergraduate Statistics Curriculum.
   *The American Statistician*, 69(4), 371–386.
   [DOI](https://doi.org/10.1080/00031305.2015.1089789);
   [author preprint](https://arxiv.org/pdf/1411.5279).
   Motivates caution about small-sample bootstrap coverage and the distinction
   between original observations and computational resamples. The manuscript's
   log-ratio t model remains an assumption-dependent prespecified choice.

6. **Transaction Processing Performance Council.** TPC Benchmark H Standard
   Specification, revision 3.0.1.
   [Official specification](https://www.tpc.org/TPC_Documents_Current_Versions/pdf/TPC-H_v3.0.1.pdf).
   Defines the complete query/refresh workload, standard timing and metrics.
   This selected-query Float64 study does not claim those metrics, compliance
   or certification. Notice and provenance questions are tracked separately in
   [NOTICE_RESEARCH.md](../NOTICE_RESEARCH.md).

7. **DuckDB.** Version 1.5 documentation, `tpch` extension.
   [Official documentation](https://duckdb.org/docs/1.5/core_extensions/tpch);
   [tagged 1.5.6 SQL query source](https://github.com/duckdb/duckdb/tree/v1.5.6/extension/tpch/dbgen/queries).
   Documents generator use, deviations from official generated data and fixed
   substitution parameters. Generator/version provenance does not establish
   benchmark compliance.

8. **Polars.** GPU Support, user guide.
   [Official documentation](https://docs.pola.rs/user-guide/gpu-support/).
   Describes GPU collection and `raise_on_fail=True`. This study additionally
   saves effective configuration and positive/unsupported session canaries;
   documentation alone does not prove execution mode.

9. **Apache Spark.** Spark 3.5.9 SQL Performance Tuning.
   [Official versioned documentation](https://spark.apache.org/docs/3.5.9/sql-performance-tuning.html).
   Describes AQE, caching, partition and join configuration, supporting retention
   of actual settings and final plans. It is not a prescription that the chosen
   fixed settings are optimal for every engine.

10. **NVIDIA.** cuDF plugin for Apache Spark, version 26.08.1 compatibility.
    [Pinned official source](https://github.com/NVIDIA/cudf-spark/blob/v26.08.1/docs/compatibility.md).
    Documents ordering and floating-point differences relevant to comparison
    semantics. Exact result checks and selected operators still require saved
    study artifacts.

11. **NVIDIA.** cuDF-Polars profiling and tracing.
    [Official versioned documentation](https://docs.nvidia.com/cudf/26.10/cudf_polars/profiling/).
    Distinguishes runtime profiling from plan interpretation. The current
    manuscript makes no kernel-time, transfer-time, or GPU-runtime-fraction
    claim. APIs must be checked against installed 26.10.2 before any additional
    diagnostic run.

12. **OpenJDK.** JDK-8192647, GCLocker allocation issue record.
    [Primary issue tracker](https://bugs.openjdk.org/browse/JDK-8192647).
    The [prospective recovery protocol](../../docs/H100_GCLOCKER100_RECOVERY_PROTOCOL.md)
    cites this record for allocation-starvation behavior and discussion of retry
    count as a possible workaround with longer-stall risk. It supplies a
    plausible mechanism, not a diagnosis of this study's Java heap-space error,
    proof of a false OOM or a causal mitigation result.

13. **OpenJDK.** JEP 423.
    [Official JEP](https://openjdk.org/jeps/423).
    Addresses G1 JNI critical-region handling in JDK 22. The recovery
    retains Java 17.0.20.1; citing this JEP does not imply that its implementation
    is present in the study runtime or that the study upgrades Java.

14. **Apache Spark.** Pull request 51920, closed unmerged.
    [Primary proposal and disposition](https://github.com/apache/spark/pull/51920).
    The recovery protocol identifies this proposal's retry-count-100 option as
    context for the separately gated fixed-profile diagnostic. A closed unmerged
    PR is not official Spark 3.5.9 tuning guidance, a validated cure or evidence
    that the profile improves performance or reliability. The fixed recovery
    configuration and retained actual driver flags determine the study's scope.
