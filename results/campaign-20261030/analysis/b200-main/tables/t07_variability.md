### Coefficient of variation per engine: within session (3 trials) and between rounds

| Engine | Within: median CV | Within: p90 | Within: max | Between: median CV | Between: p90 | Between: max |
|---|---|---|---|---|---|---|
| Polars CPU | 3.3% | 12.6% | 75.5% | 2.1% | 6.6% | 24.8% |
| Polars GPU | 3.4% | 10.4% | 18.7% | 2.8% | 7.0% | 16.8% |
| Spark CPU | 4.5% | 16.4% | 40.2% | 5.8% | 15.7% | 34.0% |
| Spark GPU | 3.1% | 8.2% | 39.9% | 1.6% | 3.8% | 9.2% |

Within-session CV uses the 3 measured trials of each cell and round; between-round CV uses the per-round medians of each cell.
