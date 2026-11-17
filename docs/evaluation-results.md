# Latest evaluation results

Updated 17 November 2026. This is the single current results summary; older incomplete model baselines have been removed.

## Canonical artifacts

- [30-case results](../evaluation/deepeval_suite/results/chunking-v2-durable-20261116-1/results.jsonl)
- [Recomputed summary](../evaluation/deepeval_suite/results/chunking-v2-durable-20261116-1/summary.json)
- [Consolidation audit](../evaluation/deepeval_suite/results/chunking-v2-durable-20261116-1/consolidation.json)
- [Application predictions](../evaluation/financial-v1/results-20261116-175816.jsonl)

The RuntimeError-only retry is consolidated into the canonical directory. Thirteen previously errored metrics across twelve cases were replaced with their latest retry outcomes. Valid original scores, including low scores, were preserved. Original/retry artifacts, request events and checkpoint histories are retained under `attempts/` inside that directory. The separate retry directory has been removed. Raw attempt files are provenance, not additional benchmarks.

## Scope and configuration

Five financial PDFs, three pages each; 30 golden questions, 55 PDF-verified evidence references, frozen reference date 2026-11-15. Generator and judge: `gpt-6.1-sol` via the configured local Codex gateway. Embeddings: pinned `BAAI/bge-base-en-v1.5`, 768 dimensions. Ingestion: Docling with `docling_source_aware_v2` chunking, 450-token maximum and soft 100-body-token target. The corpus has 44 chunks/embeddings; replaying the earlier settings on the same extraction produced 60 chunks.

Evaluation protocol: `durable_batched_v2`. Contextual relevance is batched with per-item/statement verdicts; answer relevance and faithfulness use one structured call each; precision/recall omit extra reason-generation calls. Faithfulness penalizes ambiguous/unsupported claims. These prompts/settings differ from older staged protocols, so their scores are not combined. Same-model generation/judging is correlated, not independent validation.

## Consolidated scorecard

All **30 cases processed**; **20 cases have no metric execution errors**, and **10 cases still have at least one error**. An execution error is not an incorrect-answer score. Means below use successfully scored cases only. There is no unprocessed case.

| Metric | Mean | Scored | Passed | Failed | Errors | Not applicable |
|---|---:|---:|---:|---:|---:|---:|
| Application integrity | 0.933 | 30 | 28 | 2 | 0 | 0 |
| Reference correctness [GEval] | 0.883 | 30 | 26 | 4 | 0 | 0 |
| Answer Relevancy | 0.962 | 29 | 27 | 2 | 0 | 1 |
| Faithfulness | 0.952 | 27 | 23 | 4 | 0 | 3 |
| Contextual Precision | 0.466 | 23 | 4 | 19 | 0 | 7 |
| Contextual Recall | 0.953 | 23 | 21 | 2 | 0 | 7 |
| Contextual Relevancy | 0.316 | 18 | 3 | 15 | 10 | 2 |

The ten remaining errors are all Contextual Relevancy: **three provider deadline errors and seven invalid/missing/duplicate context-ID responses**. They remain unscored. All other applicable metric executions now have scores. Do not report the context-relevance mean as coverage of all 30 cases.

Remaining error cases: FIN-001, FIN-012, FIN-013, FIN-015, FIN-016, FIN-021, FIN-026, FIN-027, FIN-029, FIN-030.

## Interpretation

- Correctness, answer relevance, strict faithfulness and context recall are comparatively strong on the scored synthetic cases; material failures remain, so these are not production accuracy claims.
- Contextual precision (0.466) and contextual relevance (0.316 on 18 scored cases) indicate substantial excess irrelevant final synthesis context. Better chunk sizes alone do not fix relevance selection, evidence packing or follow-up reasoning.
- The context metrics use evidence-ledger order, not raw vector/keyword candidate ranking. MRR/nDCG are not measured; they require ordered retrieval artifacts and reviewed relevance labels.
- Application lexical/integrity diagnostics passed 27/30. FIN-010 omitted the additional CFO approver; FIN-011 answered no early destruction but omitted benchmark-required retention/citation detail; FIN-030 is an apparent date-format false negative. These diagnostics are distinct from the DeepEval Application integrity metric (28/30).
- Per-case seconds/request counters describe their recorded invocations and may include checkpoint reuse. Original and retry invocation counters are retained separately. Use request-event history for actual attempts; do not add case timings as an application response-time SLA.
- Returned token usage is logged where available. `evaluation_cost: null` means unknown monetary cost; no configured/billed price is inferred from Codex subscription usage. Failed requests can have unknown usage.

## Current limits and next work

The detached runner has atomic per-metric checkpoints, compatible resume, capped attempts, endpoint locking and provider-failure handling. It does not guarantee upstream cancellation/exactly-once billing, automatically restart after reboot, run Temporal, or automatically publish evaluator scores into Langfuse. Source traces and the golden dataset exist in Langfuse, but local evaluator scores remain local.

See the consolidated [automation and durability plan](evaluation-automation-plan.md) for current commands, next-phase Temporal orchestration, and the subsequent LangGraph evaluator assessment. No additional model calls were made during this cleanup/consolidation.
