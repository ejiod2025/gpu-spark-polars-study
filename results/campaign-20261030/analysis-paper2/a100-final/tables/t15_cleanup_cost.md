### verified_gc cleanup cost (outside the query timers)

| Suite | Scale | Engine | Per cleanup (s) | Max (s) | Per session (s) | Share of wall |
|---|---|---|---|---|---|---|
| complex | SF30 | Polars CPU | 0.00758 | 0.0110 | 0.182 | 0.2% |
| complex | SF30 | Polars GPU | 0.0219 | 0.0290 | 0.351 | 2.9% |
| complex | SF30 | Spark CPU | 2.83 | 4.08 | 68.0 | 23.7% |
| complex | SF30 | Spark GPU | 2.87 | 4.15 | 68.9 | 41.7% |
| complex | SF100 | Polars CPU | 0.00798 | 0.0142 | 0.192 | 0.1% |
| complex | SF100 | Polars GPU | 0.0216 | 0.0311 | 0.346 | 1.2% |
| complex | SF100 | Spark CPU | 3.84 | 7.15 | 92.2 | 13.7% |
| complex | SF100 | Spark GPU | 4.04 | 8.23 | 97.1 | 29.5% |
| complex | SF300 | Polars CPU | 0.00795 | 0.0143 | 0.191 | 0.0% |
| complex | SF300 | Polars GPU | 0.0208 | 0.0303 | 0.333 | 0.3% |
| complex | SF300 | Spark CPU | 6.66 | 17.2 | 160 | 10.1% |
| complex | SF300 | Spark GPU | 6.77 | 18.4 | 162 | 18.0% |
