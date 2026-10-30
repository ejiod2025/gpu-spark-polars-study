### Coefficient of variation per engine: within session (3 trials) and between rounds

| Engine | Within: median CV | Within: p90 | Within: max | Between: median CV | Between: p90 | Between: max |
|---|---|---|---|---|---|---|
| Polars CPU | 0.7% | 2.3% | 7.2% | 0.6% | 1.4% | 2.7% |
| Polars GPU | 2.1% | 15.7% | 35.7% | 1.2% | 6.6% | 25.8% |
| Spark CPU | 4.2% | 19.1% | 40.1% | 4.1% | 19.8% | 49.8% |
| Spark GPU | 2.1% | 4.2% | 25.3% | 1.1% | 3.7% | 12.0% |

Within-session CV uses the 3 measured trials of each cell and round; between-round CV uses the per-round medians of each cell.
