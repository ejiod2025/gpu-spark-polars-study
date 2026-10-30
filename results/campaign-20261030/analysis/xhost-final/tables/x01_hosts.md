### Host identity (one complete campaign per host; reference first in comparisons)

| Host | GPU | Device GiB | CPU model | Arch | vCPU | --cpus | Threads / core (SMT) | Physical cores | NUMA nodes | Cores occupied by --cpus threads | RAM GiB | Driver | CUDA | RAPIDS jar | Rounds |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| A100 | NVIDIA A100-SXM4-40GB | 40 | AMD EPYC 7J13 64-Core Processor | x86_64 | 30 | 26 | 1 | 30 | 1 | 26 | 216.3 | 570.148.08 | 12.8 | rapids-4-spark_2.12-26.08.1.jar | 4 |
| H100 (ref.) | NVIDIA H100 80GB HBM3 | 79.6 | Intel(R) Xeon(R) Platinum 8480+ | x86_64 | 26 | 26 | 2 | 13 | 1 | 13 | 221.2 | 580.105.08 | 13.0 | rapids-4-spark_2.12-26.08.1.jar | 4 |
| GH200 | NVIDIA GH200 480GB | 95.6 | Neoverse-V2 | aarch64 | 64 | 26 | 1 | 64 | 9 | 26 | 525.7 | 570.148.08 | 12.8 | rapids-4-spark_2.12-26.08.1-cuda12-arm64.jar | 4 |
| B200 | NVIDIA B200 | 179.1 | INTEL(R) XEON(R) PLATINUM 8592+ | x86_64 | 26 | 26 | 2 | 13 | 1 | 13 | 354.1 | 570.148.08 | 12.8 | rapids-4-spark_2.12-26.08.1.jar | 4 |

Identity is read from environment.json and campaign.json; a dash marks a field that was not recorded. Topology (threads per core, physical cores, NUMA nodes) is what lscpu reports inside the machine; the cores occupied are bounded below by packing the --cpus threads onto sibling hardware threads and above by spreading them over distinct cores.
