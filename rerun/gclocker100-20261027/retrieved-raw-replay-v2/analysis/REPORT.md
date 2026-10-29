# H100 confirmatory paired-block analysis

Campaign `h100-confirmatory-gclocker100-20261027`; 400 sessions; ten paired blocks.

Each suite score sums six session query medians; it is not an observed suite wall time. Ratios above one favor the GPU. Intervals are pointwise 95%, not simultaneous. The 26 two-sided primary tests form one Holm family. Inner actions do not increase N.

| Comparison | Suite | SF | Geometric speedup | Pointwise 95% CI | Holm p | Supported GPU advantage |
|---|---|---:|---:|---|---:|---|
| polars_cpu_200 / spark_gpu | baseline | 1 | 0.056× | [0.055, 0.057] | 4.003e-18 | False |
| polars_cpu_200 / spark_gpu | baseline | 10 | 0.213× | [0.209, 0.216] | 3.574e-16 | False |
| polars_cpu_200 / spark_gpu | baseline | 30 | 0.389× | [0.382, 0.395] | 7.845e-15 | False |
| polars_cpu_200 / spark_gpu | baseline | 100 | 0.592× | [0.581, 0.603] | 2.526e-12 | False |
| polars_cpu_200 / spark_gpu | baseline | 300 | 0.698× | [0.687, 0.710] | 1.796e-11 | False |
| polars_cpu_200 / spark_gpu | complex | 30 | 0.467× | [0.432, 0.505] | 1.143e-08 | False |
| polars_cpu_200 / spark_gpu | complex | 100 | 0.524× | [0.515, 0.533] | 2.27e-13 | False |
| polars_cpu_200 / spark_gpu | complex | 300 | 0.493× | [0.479, 0.507] | 6.319e-12 | False |
| spark_cpu / spark_gpu | baseline | 1 | 0.737× | [0.685, 0.792] | 5.545e-06 | False |
| spark_cpu / spark_gpu | baseline | 10 | 1.529× | [1.451, 1.610] | 3.595e-08 | True |
| spark_cpu / spark_gpu | baseline | 30 | 2.397× | [2.300, 2.498] | 2.182e-11 | True |
| spark_cpu / spark_gpu | baseline | 100 | 3.119× | [3.056, 3.183] | 1.11e-14 | True |
| spark_cpu / spark_gpu | baseline | 300 | 3.296× | [3.239, 3.355] | 2.346e-15 | True |
| spark_cpu / spark_gpu | complex | 30 | 3.976× | [3.678, 4.298] | 7.401e-11 | True |
| spark_cpu / spark_gpu | complex | 100 | 3.521× | [3.473, 3.570] | 1.917e-16 | True |
| spark_cpu / spark_gpu | complex | 300 | 2.606× | [2.557, 2.656] | 2.656e-14 | True |
| polars_cpu_144 / polars_gpu | baseline | 1 | 0.402× | [0.390, 0.414] | 1.452e-12 | False |
| polars_cpu_144 / polars_gpu | baseline | 10 | 1.492× | [1.475, 1.509] | 4.267e-13 | True |
| polars_cpu_144 / polars_gpu | baseline | 30 | 2.554× | [2.513, 2.595] | 7.928e-15 | True |
| polars_cpu_144 / polars_gpu | baseline | 100 | 3.187× | [3.126, 3.250] | 7.621e-15 | True |
| polars_cpu_144 / polars_gpu | baseline | 300 | 3.537× | [3.473, 3.602] | 1.975e-15 | True |
| polars_cpu_200 / polars_gpu | baseline | 1 | 0.373× | [0.365, 0.381] | 3.582e-14 | False |
| polars_cpu_200 / polars_gpu | baseline | 10 | 1.281× | [1.266, 1.296] | 2.182e-11 | True |
| polars_cpu_200 / polars_gpu | baseline | 30 | 1.954× | [1.919, 1.990] | 3.283e-13 | True |
| polars_cpu_200 / polars_gpu | baseline | 100 | 2.372× | [2.321, 2.425] | 2.155e-13 | True |
| polars_cpu_200 / polars_gpu | baseline | 300 | 2.451× | [2.405, 2.498] | 4.093e-14 | True |

The t model assumes independent, approximately normal block log ratios. Ten blocks provide no power guarantee; temporal dependence and single-host scope remain limitations. A nonsignificant result is not equivalence. Zero observed log variance, if present, is explicitly flagged in JSON and does not establish absence of variability.

Polars GPU complex coverage remains four of six queries: the original regex and window rejections have no substituted latency or complete-suite aggregate. Query and host-result intervals are exploratory and have no additional hypothesis tests.

Host-result versus action point-estimate direction changes: 0. The JSON names each changed comparison; this descriptive check is not another significance test.

See analysis.json for every session/query estimate and descriptive contrast, session_query_medians.csv for nested session observations, and block_ratios.csv for all paired block observations, including unsupported outcomes.
