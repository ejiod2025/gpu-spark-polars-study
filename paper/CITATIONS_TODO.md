# Citations not yet in references.bib

Everything in `references.bib` was verified on 2026-10-29 (Crossref metadata for
DOI entries; the publisher page or the URL itself otherwise). Items below are
deliberately absent until someone verifies them.

| Candidate | Why it would help | Status |
|---|---|---|
| A citable cuDF / RAPIDS paper or Zenodo release for cuDF 26.10 | Background on cuDF itself; currently cited via documentation only | No peer-reviewed cuDF paper located; check https://github.com/rapidsai/cudf for a CITATION file or Zenodo DOI |
| A peer-reviewed description of the Spark GPU plugin (RAPIDS Accelerator for Apache Spark) | Related work on GPU Spark; currently cited via docs and repository | Not located; do not cite NVIDIA marketing benchmarks as peer-reviewed |
| NVIDIA H100 (Hopper) architecture whitepaper | Hardware background (HBM3 bandwidth, SM count) if the discussion needs it | Not fetched; cite only if the paper uses a specific figure from it |
| Lambda `gpu_1x_h100_sxm5` instance documentation | Host description | URL in `docs/POLARS_GPU_COMPAT.md` §11 (https://docs.lambda.ai/public-cloud/on-demand/); not fetched for this draft |
| Independent (non-vendor) Polars GPU engine evaluations | Related work: "independent replicated comparisons remain scarce" claim in §2 | Literature search not completed; the claim is worded as "to our knowledge" and must be re-checked before submission |
| GPU join/aggregation papers beyond Shanbhag et al. and Lutz et al. (e.g. hash-join studies on GPUs) | Related work depth | Not verified; add with DOIs only |
| TPC-DS / NDS papers | Only if the paper discusses TPC-DS-derived suites | Not needed for current text |
| The earlier A100 study (this project) | Prior work in §1 | Owner-private site; cite once a public, stable URL or report exists |
| ~~NVIDIA GH200 Grace Hopper Superchip whitepaper / datasheet~~ (product page now cited as `nvidia_gh200`; whitepaper still not fetched) | §3.3 and §6 describe the GH200 (Grace CPU, coherent NVLink-C2C CPU–GPU link); currently stated qualitatively without a citation | Not fetched; cite if the discussion uses a specific bandwidth or memory figure |
| NVIDIA Blackwell (B200) architecture documentation | Hardware background for the B200 rung | Not fetched; only needed for specific hardware figures |
| Lambda instance documentation (checked 2026-10-30: https://docs.lambda.ai/public-cloud/on-demand/ lists A100 SXM 1 GPU with 220 GiB RAM, not the 200 GiB quoted in A3; H100 225, GH200 432, B200 360 GiB match). Not cited: the paper compares measured values with the figures quoted in the design | Hosts table `tab:hosts` (vCPU and RAM figures, taken from the design document A2/A3) | Not fetched; verify against the instance record in each `campaign.json` |
| ~~NVIDIA CUDA compatibility documentation~~ (now cited as `nvidia_cuda_compat`) | §3.3 sentence on hosts whose driver supports an older CUDA minor version than the 12.9 runtime | Not fetched; cite the CUDA Compatibility guide page if the sentence is kept |
