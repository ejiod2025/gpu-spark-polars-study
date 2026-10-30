### What differs between the 2026-10-25 A100 study and the H100 campaign (read from the result files)

| Field | Engines | Suites | A100 study | H100 campaign | Engine in the ratio |
|---|---|---|---|---|---|
| library version | Polars CPU | baseline, complex | 2.0.0 | 1.44.2 | no |
| threads | Polars CPU, Spark CPU, Spark GPU | baseline, complex | 30 | 26 | yes |
| sessions per cell | all | all | 1 | 4 | yes |
| engine order | all | all | spark_cpu > polars > spark_gpu / spark_gpu > polars > spark_cpu / spark_gpu > spark_cpu > polars | polars > spark_gpu > polars_gpu > spark_cpu / polars_gpu > polars > spark_cpu > spark_gpu / spark_cpu > polars_gpu > spark_gpu > polars / spark_gpu > spark_cpu > polars > polars_gpu | yes |
| query cleanup policy | Polars CPU, Spark CPU, Spark GPU | baseline | not recorded | none | yes |
| failure policy | Polars CPU, Spark CPU, Spark GPU | baseline | not recorded | raise | yes |
| failure policy | Polars CPU, Spark CPU, Spark GPU | complex | not recorded | record_and_continue | yes |
| suite version | Polars CPU, Spark CPU, Spark GPU | baseline | not recorded | baseline-v1 | yes |
| Spark GPU profile | Spark GPU | baseline, complex | not recorded | a100-parity | yes |
| Spark jars | Spark GPU | baseline, complex | not recorded | rapids-4-spark_2.12-26.08.1.jar | yes |
| GPU | all | all | NVIDIA A100-SXM4-40GB | NVIDIA H100 80GB HBM3 | yes |
| vCPUs | all | all | 30 | 26 | yes |
| host RAM (GiB) | all | all | 216 | 221.2 | yes |
| spark.master | Spark GPU | all | local[30] | local[26] | yes |
| spark.rapids.sql.multiThreadedRead.memoryLimit.size | Spark GPU | all | 8 values | 32 values | yes |

'not recorded': the earlier study's result files carry no such field. CPU engines are not compared across the two.
