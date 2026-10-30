### verified_gc cleanup cost (outside the query timers)

| Suite | Scale | Engine | Per cleanup (s) | Max (s) | Per session (s) | Share of wall |
|---|---|---|---|---|---|---|
| complex | SF30 | Polars CPU | 0.00563 | 0.00617 | 0.135 | 0.3% |
| complex | SF30 | Polars GPU | 0.0171 | 0.0185 | 0.274 | 3.1% |
| complex | SF30 | Spark CPU | 2.43 | 2.68 | 58.3 | 29.8% |
| complex | SF30 | Spark GPU | 2.50 | 2.77 | 59.9 | 43.1% |
| complex | SF100 | Polars CPU | 0.00571 | 0.00653 | 0.137 | 0.1% |
| complex | SF100 | Polars GPU | 0.0188 | 0.0201 | 0.300 | 1.6% |
| complex | SF100 | Spark CPU | 2.71 | 4.52 | 65.0 | 14.6% |
| complex | SF100 | Spark GPU | 2.70 | 4.64 | 64.9 | 29.8% |
| complex | SF300 | Polars CPU | 0.00577 | 0.00678 | 0.139 | 0.0% |
| complex | SF300 | Polars GPU | 0.0192 | 0.0214 | 0.308 | 0.6% |
| complex | SF300 | Spark CPU | 3.64 | 7.15 | 87.5 | 8.3% |
| complex | SF300 | Spark GPU | 3.41 | 6.98 | 81.9 | 19.2% |
