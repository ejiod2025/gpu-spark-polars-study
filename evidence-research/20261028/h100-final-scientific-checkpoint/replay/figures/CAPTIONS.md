# Figure captions

## suite_ratios_baseline

Each small point is one of ten paired block ratios. Diamonds and bars show the geometric mean and pointwise 95% Student-t interval on block log ratios; intervals are not simultaneous. The 26 prespecified suite tests form one Holm family; adjusted p values are in primary_contrasts.csv. Ratio >1 favors the GPU. Each session score sums six query medians from three inner actions; this is not an observed suite latency. Action time excludes startup. Blocks, rather than inner actions, are the inference units. Independent, approximately normal block log ratios are assumed; ten blocks do not guarantee power.

## suite_ratios_complex

Each small point is one of ten paired block ratios. Diamonds and bars show the geometric mean and pointwise 95% Student-t interval on block log ratios; intervals are not simultaneous. The 26 prespecified suite tests form one Holm family; adjusted p values are in primary_contrasts.csv. Ratio >1 favors the GPU. Each session score sums six query medians from three inner actions; this is not an observed suite latency. Action time excludes startup. Blocks, rather than inner actions, are the inference units. Independent, approximately normal block log ratios are assumed; ten blocks do not guarantee power.

## query_ratios_action

Cells show geometric means of ten paired block query ratios for CPU Polars 2.0.0 / GPU, using the action endpoint. Color encodes log2(ratio), centered symmetrically on 1×. These query comparisons are descriptive; no query significance tests or multiplicity-adjusted query claims are made. Gray N/A cells retain the original strict GPU regex and grouped-window rejections. They are not a ratio of 1 and receive no imputed latency. The original correlated query does not establish an executed nonempty anti join.

## complete_case_scores

Values are descriptive geometric means of ten complete session scores in seconds. Each score sums six query medians, not an observed whole-suite latency. Every cell reports planned-block completeness and query coverage. Polars GPU complex retains four of six supported original queries and has zero complete six-query scores; a partial four-query sum is never substituted. Versions are the fixed study configuration; the renderer does not independently inventory hardware or runtimes.
