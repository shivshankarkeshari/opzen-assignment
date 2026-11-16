# Performance and cost

## Measured prototype behavior

[Raw measurements](performance-measurements.json) were collected on 16 November 2026 against the local Docker API, with one client, five bundled PDFs and five requests per operation. Search used `minimum retention onboarding files`, current scope and four results. No answer-generation calls were made. Hardware was not controlled; this is a smoke benchmark, not a load/capacity test. The nearest-rank p95 of five samples is simply their maximum and is not a stable production percentile.

| Operation | Median | Sample p95/max | Requests |
|---|---:|---:|---:|
| Health | 6.29 ms | 12.62 ms | 5 |
| Authorized document library | 10.67 ms | 18.42 ms | 5 |
| Hybrid search, including query encoding | 97.32 ms | 366.27 ms | 5 |

All library requests returned five authorized documents; each search returned four hits. The stored corpus previously verified 60 normalized 768-dimensional BGE vectors for 60 chunks. The UI replay passed 64/64 checks. These results do not measure ingestion/OCR throughput, end-to-end answer latency or concurrent users; those measurements remain a production-readiness gap.

The completed first-pass evaluation has per-case `seconds` and `judge_requests` in [results.jsonl](../evaluation/deepeval_suite/results/codex-gpt-6.1-sol-30/results.jsonl). These are evaluation timings, not application answer timings. It recorded 69 judge-metric errors across 20 cases. Codex gateway deadlines and per-passage judging make it unsuitable to infer an application latency SLA from that run.

## One-million-document sizing assumptions

This is a capacity model, not a claim of tested scale. Assume 1 million originals averaging 1 MB each, 30 chunks/document, and 768 float32 dimensions. Originals occupy about 1 TB; allowing another 2 TB for canonical data, previews and artifacts gives a 3 TB planning baseline before replication and retained versions. Thirty million vectors alone require 92.16 GB (decimal); allow roughly 2–4 times that for a preliminary vector/index capacity envelope, then size metadata, full-text indexes, replicas and backups separately using real data.

At 1% changed documents/day, 10,000 documents and approximately 300,000 chunks/day need processing. At 200 tokens/chunk that is about 60 million embedding-input tokens/day. Local embedding cost is worker compute, not zero cost; measure CPU/GPU throughput and OCR share before choosing worker counts. Unchanged original hashes and compatible processing artifacts can reduce this workload.

## Illustrative monthly budget

Assume 20,000 questions/day for 30 days, three LLM calls/question, 4,000 input and 500 output tokens/call. This yields 7.2 billion input and 0.9 billion output tokens/month. At **hypothetical planning rates** of $1/million input tokens and $4/million output tokens, generation is $10,800/month. These rates are arithmetic assumptions, not current quotes for Groq, OpenAI, Codex or any other provider.

Adding an illustrative $1,500–$4,000/month infrastructure envelope for storage, database/index, ingestion/embedding workers, observability and backups gives **$12,300–$14,800/month**, before separately priced OCR, egress, tax and operations labor. Worker sizing, retained versions, region and provider prices must be validated before procurement. This is not a deployment quote. Doubling average model calls approximately doubles generation cost; the prototype has hard bounds but actual investigation depth varies.

Formula: `monthly requests × calls/request × (input_tokens × input_rate + output_tokens × output_rate) / 1,000,000 + infrastructure + OCR + network + operations`.

No provider invoice, actual billed cost per question or production infrastructure bill was measured for this prototype. Existing ChatGPT access through the optional Codex CLI gateway is not an API billing estimate. Use Langfuse usage observations and timed load tests with dated provider quotes for the next estimate.
