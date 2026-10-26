# A100 Spark and Polars research draft

This buildless website presents measured comparisons of CPU Polars, CPU Spark,
and NVIDIA GPU-accelerated Spark on shared Parquet inputs. The complex-query
suite includes multi-table, outer and self joins, ordered windows, and exact
distinct counts. Its results remain separate from the original six-query suite.

The deployment serves `dist/` and uses public Sites access, as requested by the
study owner. Research artifacts retain their source provenance and notice references.
The page and evidence download state their measured coverage and limitations.

The source HTML, CSS and JavaScript are ordinary files inside `dist/`. Numerical
tables come from validated reports; the attached draft evidence ZIP contains
trial outputs, final plans, input manifests, methods and source provenance.
No dataset files, dependency binaries, credentials or machine inventories are
part of this website.

When packaging on macOS, run the Sites source workflow with
`COPYFILE_DISABLE=1`. This omits AppleDouble metadata sidecars from the deployment
archive while preserving the measured data and configured static assets.
