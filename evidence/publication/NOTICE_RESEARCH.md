# Publication notice research

Checked 2026-10-25 against primary TPC and DuckDB sources. This records published terms and unresolved provenance questions; it is not legal advice or publication clearance. No external contact or publication was made. No written TPC permission was supplied to this audit.

## What the sources establish

Results publication, source redistribution, and benchmark-name use have separate conditions. A noncompliance disclaimer alone does not settle all three.

### Current TPC policy

The [official current-document index](https://www.tpc.org/tpc_documents_current_versions/current_specifications5.asp?mode=CURRENT-ONLY) links Policies **6.20**, November 2024. The linked PDF's cover and revision history say 6.20; some page footers still say 6.19. Use the current linked document, rather than old indexed Policies 6.4.

[Policies 6.20, §§8.1–8.3, printed pp.58–60](https://www.tpc.org/TPC_Documents_Current_Versions/pdf/Policies_v6.20.pdf#page=58):

- §8.1.4 requires **“Derived from”** before benchmark-name instances describing derived work, and says parties **“must secure the TPC’s written permission.”** It disallows the standard's primary/optional metrics for derived work.
- §8.1.5 prescribes a disclaimer beginning **“This workload is derived from”**. It must identify derivation, noncomparability, and noncompliance. Retrieve its complete sentence directly from that clause.
- §8.3 permits nonstandard benchmarks subject to material-use/fair-use provisions and explicit deviations/noncomparability.
- §8.1.2 conditions distribution permission on no fee, dissemination purpose, copyright/title/date attribution, and acknowledgment of copying permission. §8.1.3 requires the software EULA.

### Result presentation and disclosure

The official [Fair Use Quick Reference 1.0.0, 2019-06-05](https://www.tpc.org/TPC_Documents_Current_Versions/pdf/Fair_Use_Quick_Reference_v1.0.0.pdf) explains that unaudited or modified studies are non-TPC benchmarks. It requires maintaining the copyright notice and license. It calls for a disclaimer wherever derived results appear: introductory report/blog text and chart/graph footnotes are examples. It says a formal Full Disclosure Report is unnecessary for nonstandard benchmarks, but sufficient environment disclosure is needed for fair comparison. It calls for explicit deviations and prohibits comparing nonstandard results with official TPC results. Its policy section numbering predates the current document; follow current clauses above where numbering differs.

For this study, retain the actual engine versions, hardware/CPU allocation, memory limits, GPU, storage, data-generation recipe and manifests, query definitions, type normalization, warmups/repeats, timing boundaries, result checks, and resource observations. Distinguish measured data from estimates. Those are concrete disclosure choices for this experiment, not a claim that the quick reference enumerates each item.

### DuckDB 1.5.6 and the embedded TPC software

The [tagged DuckDB root LICENSE](https://raw.githubusercontent.com/duckdb/duckdb/v1.5.6/LICENSE) contains the MIT grant and **“Copyright 2018-2025 Stichting DuckDB Foundation”**. Copies or substantial portions require that copyright and permission notice. This does not by itself establish a license for separately noticed third-party material or for this repository's original contributions.

The tagged [extension/tpch/dbgen/LICENSE](https://raw.githubusercontent.com/duckdb/duckdb/v1.5.6/extension/tpch/dbgen/LICENSE) is **TPC EULA version 2.2**. The [TPC-hosted EULA 2.2.0](https://www.tpc.org/TPC_Documents_Current_Versions/txt/EULA_v2.2.0.txt) contains the same relevant clauses:

- §4(b) permits modification. §4(c)(2) permits academic/research performance disclosure without a marketing position. §4(c)(3) permits other uses when results are clearly noncomparable to official results, absent specific authorization.
- §§5–8 preserve licenses/notices and terms on integrated portions.
- §9 permits redistribution: original portions with the complete EULA; modified software under terms providing at least its protections/conditions. Preserve notices and charge no distribution fee for that software, including modifications.
- §9(b) requires the legend **“THE TPC SOFTWARE IS AVAILABLE WITHOUT CHARGE FROM TPC.”** at the top of the label/license, in capitals, at least 12 point and at least equally prominent. Plain Markdown does not establish that rendered size.

Preserve the complete upstream license unchanged with covered software; this summary is not a replacement.

### Query provenance and notices

The [complete tagged Git tree](https://api.github.com/repos/duckdb/duckdb/git/trees/v1.5.6?recursive=1) contains `extension/tpch/dbgen/LICENSE` and the SQL queries below; it contains no separate `extension/tpch/dbgen/queries/LICENSE`. This is a checked inventory, not a legal determination about each query's copyrightability or the scope of derivation.

| Local workload | Tagged upstream query reference |
| --- | --- |
| Baseline q01 | [q01.sql](https://raw.githubusercontent.com/duckdb/duckdb/v1.5.6/extension/tpch/dbgen/queries/q01.sql) |
| Baseline q03 | [q03.sql](https://raw.githubusercontent.com/duckdb/duckdb/v1.5.6/extension/tpch/dbgen/queries/q03.sql) |
| Baseline q06 | [q06.sql](https://raw.githubusercontent.com/duckdb/duckdb/v1.5.6/extension/tpch/dbgen/queries/q06.sql) |
| Baseline q10 | [q10.sql](https://raw.githubusercontent.com/duckdb/duckdb/v1.5.6/extension/tpch/dbgen/queries/q10.sql) |
| Baseline q12 | [q12.sql](https://raw.githubusercontent.com/duckdb/duckdb/v1.5.6/extension/tpch/dbgen/queries/q12.sql) |
| Baseline q14 | [q14.sql](https://raw.githubusercontent.com/duckdb/duckdb/v1.5.6/extension/tpch/dbgen/queries/q14.sql) |
| Complex suite Q9 derivation | [q09.sql](https://raw.githubusercontent.com/duckdb/duckdb/v1.5.6/extension/tpch/dbgen/queries/q09.sql) |
| Complex suite Q13 derivation | [q13.sql](https://raw.githubusercontent.com/duckdb/duckdb/v1.5.6/extension/tpch/dbgen/queries/q13.sql) |

The tagged [dbgen shared.h](https://raw.githubusercontent.com/duckdb/duckdb/v1.5.6/extension/tpch/dbgen/include/dbgen/shared.h) supplies an actual ownership notice: **“Copyright owned by the Transaction Processing Performance Council.”** Preserve upstream notices on extracted/copied material even when the extracted SQL lacks an inline header. Treat separately authored native implementations as a provenance-review question; neither ownership nor relicensing rights have been established by this audit.

The [official benchmark specification, revision 3.0.1](https://www.tpc.org/TPC_Documents_Current_Versions/pdf/TPC-H_v3.0.1.pdf#page=5), dated 28 April 2022, shows **“© 1993 - 2022 Transaction Processing Performance Council”**. Page 5 conditions no-fee dissemination and separately requires written permission for other copying/distribution, expressly including incorporation into a non-TPC document, specification, or report. This matters if reproducing specification text/tables beyond referencing the software. Do not invent a copyright year for dbgen by copying the specification's year range onto unrelated files.

DuckDB's [version 1.5 extension documentation](https://duckdb.org/docs/1.5/core_extensions/tpch) warns that generated databases differ slightly from the official specification and that bundled queries use fixed substitution parameters. Thus the generator/version and query source must be disclosed; using its generator does not establish compliance.

## Local notice material for the publication owner

Recommended attribution inventory, to accompany the complete applicable licenses and the exact policy disclaimer:

> Data-generation dependency: DuckDB 1.5.6 `tpch` extension. Selected SQL workload definitions were adapted from its tagged `extension/tpch/dbgen/queries` sources. TPC-origin software remains subject to the TPC EULA version 2.2; see the linked upstream license and retained notices. DuckDB-origin material carries its applicable upstream license. Original project contribution licensing is to be specified by its authorized owner.

Add the exact §9(b) legend above wherever its distribution requirements apply, retain the copyright notice, and include the source version/URLs. Do not label the whole research bundle MIT without resolving the original contribution license and third-party terms.

Add the §8.1.5 sentence separately. Suggested supplemental experiment explanation:

> This is an unaudited research experiment with selected queries, additional custom workloads, floating-point normalization, and its own timing methodology. Reported timings describe only these implementations and inputs; they are not official benchmark submissions.

This supplemental text does not replace the prescribed disclaimer or establish permission for benchmark-name use.

## Publication work still to resolve

1. Establish the §8.1.4 permission status for the proposed title, labels, captions, and attribution. The sources list `info@tpc.org`; this audit did not send a message.
2. Confirm which upstream portions are redistributed, preserve their complete applicable licenses/notices, and resolve the modified-software licensing condition rather than assuming MIT.
3. Record permission/provenance for any copied specification text separately. Decide the license for original project contributions with the authorized owner.
4. Check result/chart notices, deviations, environment disclosure, and metric/comparison wording against the findings above.

The documented grants support a conditional publication route. The evidence does not establish that this bundle, as currently prepared, has completed every condition.
