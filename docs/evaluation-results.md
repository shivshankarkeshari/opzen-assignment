# Evaluation results

These are synthetic development-regression results, not held-out production accuracy. The model used for generation and judging was Codex CLI `gpt-6.1-sol`; same-model judging can produce correlated errors.

## Application and UI

- Five original PDFs, three pages each; 30 golden cases and 55 PDF-verified references.
- Final application predictions: 28/30 automatic diagnostics pass; no provider-blocked application outcomes remain after seven explicitly documented retries.
- FIN-011 omits the seven-year retention minimum and the second required document citation.
- FIN-030 gives the correct equivalent date “December 31, 2026”; its lexical ISO-date check falsely fails.
- Streamlit replay: 64/64 rendering checks; displaying an answer is not a semantic quality pass.

## Completed first DeepEval pass

| Metric | Mean of valid scores | Passed/scored | Errors | Not applicable |
|---|---:|---:|---:|---:|
| Application integrity | 0.967 | 29/30 | 0 | 0 |
| Reference correctness [GEval] | 0.900 | 15/16 | 14 | 0 |
| Answer Relevancy | 0.971 | 16/17 | 12 | 1 |
| Faithfulness | 1.000 | 13/13 | 14 | 3 |
| Contextual Precision | 0.527 | 3/16 | 7 | 7 |
| Contextual Recall | 0.953 | 15/16 | 7 | 7 |
| Contextual Relevancy | 0.417 | 2/13 | 15 | 2 |

The run attempted all 30 cases. Sixty-nine judge-metric execution errors occurred across 20 cases; they are neither zero-valued quality scores nor successful grades. One case passed every applicable metric in this first pass. Partial later retries were stopped on request and archived; they are not combined into this scorecard.

FIN-011 scores 0.6 for reference correctness despite 1.0 faithfulness: its claims are supported, but its answer is incomplete. FIN-027 context recall is 0.5 because the reference includes an application security expectation absent from document evidence. FIN-028 is a correct no-source API rejection with a generic harness message and Answer Relevancy 0.5, not a generated-answer example.

Contextual Precision evaluates the actual synthesis-ledger order, which is evidence-ID order rather than raw vector/RRF rank. There are 480 delivered passages across 28 nonempty contexts (median 11, maximum 48). The compare tool loads whole selected revisions. Low context precision/relevance therefore cannot be attributed solely to embedding quality. No vector dimension/profile/normalization defect was found in the inspected 60-vector corpus.

## Reproducible artifacts

- [Golden dataset](../evaluation/financial-v1/golden.jsonl), [source manifest](../evaluation/financial-v1/manifest.json), [validation](../evaluation/financial-v1/validation.json).
- [Saved 30 application predictions](../evaluation/financial-v1/results-codex-gpt-6.1-sol-20261116-30.jsonl) and [attempt provenance](../evaluation/financial-v1/codex-all-30-manifest.json). Supporting partial development batches were removed from the submission and archived locally. Historical source-file strings in the consolidated rows are audit labels; the consolidated file remains byte-for-byte unchanged.
- [Completed DeepEval summary](../evaluation/deepeval_suite/results/codex-gpt-6.1-sol-30/summary.json) and [per-case metrics/reasons](../evaluation/deepeval_suite/results/codex-gpt-6.1-sol-30/results.jsonl).
- [UI replay](../output/pdf/financial-ui-tests/ui-results-codex-all-30.json).
- [Manual evaluation instructions](../evaluation/deepeval_suite/RUNNING.md).

The source/model failures and low scores are intentionally preserved. No model-based evaluation was run or altered during submission cleanup.

## Restored LFM comparison and planned automation

The historical 30-case LFM evaluation is restored in [20261115T134006Z](../evaluation/deepeval_suite/results/20261115T134006Z/summary.json), with original case records and matching application predictions. It measured only application integrity, reference correctness and faithfulness. See [the model comparison](lfm-vs-gpt-sol-findings.md) for why its low scores cannot be attributed simply to chunk size or embedding dimension.

See [the automation plan](evaluation-automation-plan.md) for current metric coverage, MRR/nDCG label requirements, latency/token/cost and retrieval-health instrumentation, CI/scheduled workflows and regression criteria. No new model evaluation or scoring-policy change was made for this plan.
