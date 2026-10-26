# CPU software-stack control

Suite: **complex**.

Primary CPU: Polars 1.44.2 / Python 3.11.13. Control CPU: Polars 2.0.0 / Python 3.10.12. Both use CPU streaming.

Python and Polars change together. This is a CPU software-stack control, not an isolated Polars version effect. The same frozen benchmark/query sources, input manifests, CPU thread allocation, timing scope, cache policy, one warmup and three measured trials, and suite cleanup lifecycle must pass validation (`none` for baseline; `verified_gc` for complex). CPU result agreement uses the original tolerances and exact integer pairs.

Validation: **PASS**. Control outputs compared: 72; output mismatches: 0; validation failures: 0.

Polars GPU values are references from the separately revalidated primary run. Only validated GPU cells are included; unsupported queries have no GPU timing or complete GPU suite score. They were not rerun under the control Python/Polars stack.

Ratios divide the first named engine's elapsed time by the second's. Above 1 favors the denominator. Suite scores sum six per-query medians; they are not once-through suite runtimes. A failed or incomplete control scale has no control score or ratio.

| Scale | Status | Primary CPU Polars 1.44.2 sum (s) | Control CPU Polars 2.0.0 sum (s) | Primary Polars GPU 1.44.2 sum (s) | Primary CPU / control CPU | Control CPU / Polars GPU |
|---|---|---:|---:|---:|---:|---:|
| sf30 | ok | 15.291226 | 5.575571 | n/a | 2.742540 | n/a |
| sf100 | ok | 58.440301 | 21.572493 | n/a | 2.709019 | n/a |
| sf300 | ok | 184.446407 | 70.388711 | n/a | 2.620398 | n/a |

## Per-query measured times

Median [minimum, maximum] seconds over the three measured trials.

| Scale / query | Status | Primary CPU Polars 1.44.2 | Control CPU Polars 2.0.0 | Primary Polars GPU 1.44.2 |
|---|---|---:|---:|---:|
| sf30 / cx_q09 | ok | 1.919544 [1.893260, 1.961003] | 1.017683 [1.008637, 1.054906] | 0.367387 [0.359475, 0.378413] |
| sf30 / cx_q13 | ok | 0.967525 [0.951181, 1.015173] | 0.709244 [0.702284, 0.733496] | n/a |
| sf30 / cx_semi_anti | ok | 0.595261 [0.586828, 0.597849] | 0.401695 [0.398663, 0.403637] | 0.229105 [0.219039, 0.231995] |
| sf30 / cx_window | ok | 8.683947 [8.597982, 8.801970] | 1.242502 [1.202038, 1.331380] | n/a |
| sf30 / cx_distinct | ok | 2.423292 [2.362823, 2.446564] | 1.784591 [1.778564, 1.806772] | 0.531704 [0.521865, 0.532421] |
| sf30 / cx_self_join | ok | 0.701657 [0.641984, 0.702881] | 0.419855 [0.417225, 0.458842] | 0.211053 [0.206951, 0.215074] |
| sf100 / cx_q09 | ok | 7.470023 [7.259929, 7.630377] | 4.265741 [4.200084, 4.326825] | 1.044448 [1.016402, 1.055807] |
| sf100 / cx_q13 | ok | 4.021408 [3.957948, 4.097547] | 2.954623 [2.948397, 2.977259] | n/a |
| sf100 / cx_semi_anti | ok | 2.064413 [1.994880, 2.228666] | 1.566833 [1.515172, 1.572139] | 0.635128 [0.625410, 0.640067] |
| sf100 / cx_window | ok | 30.885270 [30.841477, 31.173784] | 4.697330 [4.669072, 4.992406] | n/a |
| sf100 / cx_distinct | ok | 11.491133 [11.233341, 11.681907] | 6.591907 [6.555842, 6.629563] | 1.415595 [1.396000, 1.424256] |
| sf100 / cx_self_join | ok | 2.508054 [2.223580, 2.577160] | 1.496059 [1.468510, 1.529318] | 0.705169 [0.674365, 0.721640] |
| sf300 / cx_q09 | ok | 23.559291 [23.405602, 26.186901] | 13.343342 [13.250888, 13.467233] | 3.679999 [3.602414, 3.749071] |
| sf300 / cx_q13 | ok | 13.442402 [13.337317, 14.062034] | 9.172199 [9.056580, 9.285755] | n/a |
| sf300 / cx_semi_anti | ok | 6.824332 [6.507193, 9.394496] | 4.734766 [4.700636, 5.481044] | 1.956865 [1.933402, 1.971626] |
| sf300 / cx_window | ok | 94.196558 [93.865749, 95.279082] | 17.258256 [17.227156, 17.872798] | n/a |
| sf300 / cx_distinct | ok | 37.060424 [36.378254, 37.613226] | 21.110662 [20.758109, 21.356948] | 4.540849 [4.518660, 4.558614] |
| sf300 / cx_self_join | ok | 9.363400 [8.690106, 9.988317] | 4.769486 [4.693336, 4.814122] | 2.901752 [2.874932, 2.925125] |

## Validation failures

None.
