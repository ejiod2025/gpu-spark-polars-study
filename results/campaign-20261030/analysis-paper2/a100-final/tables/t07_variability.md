### Coefficient of variation per engine: within session (3 trials) and between rounds

| Engine | Within: median CV | Within: p90 | Within: max | Between: median CV | Between: p90 | Between: max |
|---|---|---|---|---|---|---|
| Polars CPU | 5.8% | 24.3% | 87.7% | 3.4% | 10.6% | 44.8% |
| Polars GPU | 2.3% | 6.9% | 22.0% | 1.8% | 4.7% | 11.6% |
| Spark CPU | 5.7% | 26.4% | 76.7% | 5.1% | 19.5% | 53.1% |
| Spark GPU | 3.0% | 7.0% | 35.0% | 2.0% | 5.3% | 10.2% |

Within-session CV uses the 3 measured trials of each cell and round; between-round CV uses the per-round medians of each cell.
