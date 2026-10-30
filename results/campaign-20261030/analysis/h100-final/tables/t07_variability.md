### Coefficient of variation per engine: within session (3 trials) and between rounds

| Engine | Within: median CV | Within: p90 | Within: max | Between: median CV | Between: p90 | Between: max |
|---|---|---|---|---|---|---|
| Polars CPU | 3.1% | 11.2% | 64.1% | 1.8% | 4.6% | 24.0% |
| Polars GPU | 2.3% | 7.7% | 35.7% | 2.1% | 6.9% | 10.5% |
| Spark CPU | 4.5% | 17.4% | 106.6% | 3.2% | 9.6% | 23.0% |
| Spark GPU | 2.3% | 7.7% | 50.1% | 1.4% | 3.1% | 31.0% |

Within-session CV uses the 3 measured trials of each cell and round; between-round CV uses the per-round medians of each cell.
