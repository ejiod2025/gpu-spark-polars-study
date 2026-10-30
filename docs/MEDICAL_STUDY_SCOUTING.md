# Medical fine-tuning study — scouting notes (deferred)

Status: **deferred until the four-engine study is finished** (user decision, 2026-10-30). Nothing here is
a locked design. Model facts were spot-checked by the lead against the Hugging Face API on 2026-10-30.

## Verified candidates (HF API, 2026-10-30)

| Model | Created | Params | License | Gated | Base |
|---|---|---:|---|---|---|
| google/medgemma-1.5-4b-it | 2026-01-07 | 4.30B | HAI-DEF (custom) | click-through | Gemma 3 |
| Qwen/Qwen3.5-9B | 2026-02-27 | 9.65B | Apache-2.0 | no | Qwen3.5-9B-Base |
| FreedomIntelligence/HuatuoGPT-3-9B | 2026-09-17 | 9.41B | Apache-2.0 | no | Qwen/Qwen3.5-9B |
| google/gemma-4-12B-it | 2026-05-23 | 11.96B | Apache-2.0 | no | google/gemma-4-12B |
| allenai/Olmo-3-7B-Instruct | 2025-11-19 | 7.30B | Apache-2.0 | no | Olmo-3 (open data) |
| Intelligent-Internet/II-Medical-8B | 2025-05-12 | 8.19B | Apache-2.0 | no | — |

## Draft design direction (M1 + M2)

- Matched pairs: A = Qwen3.5-9B vs HuatuoGPT-3-9B (same base); B = Gemma 3 4B-it vs MedGemma 1.5 4B;
  control = Olmo-3-7B (auditable contamination); newest base = Gemma 4 12B.
- Factors: full FT vs LoRA vs QLoRA (M1) × evaluation at bf16 / FP8 / INT4 (M2) × 3 seeds.
- Hardware: H100 80 GB for LoRA/QLoRA and full FT up to ~9B with an 8-bit optimizer; B200 180 GB for full FT.
- Pre-registered risk: Unsloth documents QLoRA as "not recommended" for Qwen3.5 (hybrid Gated-DeltaNet).
- Benchmarks: MedQA, MedMCQA, PubMedQA, MMLU-Med (likely contaminated) + MedXpertQA (hard, 2025) +
  HealthBench (fixed, pre-registered judge) + LiveMedBench / n-gram check vs OLMo data for contamination.
- Stacks: TRL ≥ 1.17, Axolotl ≥ 0.21.1, Unsloth; torchtune is unmaintained (exclude). transformers ≥ 5.
- Skip: gpt-oss (MXFP4 confounds quantization), Llama 4 (MoE + manual gating), Baichuan-M3 (235B).
- Cost estimate: ~40–70 GPU-hours (~$200–400), after a 2–3 h throughput pilot.

## Open items before locking a design
- User: accept MedGemma HAI-DEF terms on HF; provide a read-only HF token (file, not chat).
- Verify: HuatuoGPT-3 and Qwen3.5/Gemma 4 medical scores; HuatuoGPT-3-27B size; HAI-DEF commercial terms;
  MedQA/MedXpertQA/HealthBench/LiveMedBench licences for redistribution of derived results.
- Research-only; no PHI on rented GPUs; benchmark accuracy ≠ clinical competence.

## Pathology foundation models (for the alternative histopathology study)
UNI2-h, CONCH v1.5, CONCH-Z (2026-10), TITAN, Virchow2: CC-BY-NC-ND, auto gating. KRONOS2, Prism2, H-optimus-1:
manual gating. Prov-GigaPath (+flash, 2026-07) and H-optimus-0: Apache-2.0. kaiko midnight: MIT. MedGemma 1.5
handles whole-slide images natively. (From the scout report; not individually re-verified by the lead.)
