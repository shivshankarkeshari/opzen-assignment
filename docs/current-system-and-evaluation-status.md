# Current system and evaluation status

Snapshot: **16 November 2026, approximately 12:17 IST**. This document distinguishes the current local configuration, the running ingestion worker, saved test evidence and features that remain proposed. No new model evaluations were started for this review.

## 1. Models and processing components used

| Component | Current local test configuration | Role and qualification |
|---|---|---|
| Answer/planning LLM | `gpt-6.1-sol`, `openai_compatible` adapter, host Codex CLI gateway | Structured plans, evidence collection, cited answer synthesis and semantic-support checking. Reasoning effort `low`; configured output budget 4,096 tokens. |
| Evaluation judge | Codex CLI / `gpt-6.1-sol`, reasoning effort `low` | DeepEval semantic metrics. Using the same model family for generation and judging creates correlated-error/self-preference risk; it is not independent acceptance evidence. |
| Embeddings | `BAAI/bge-base-en-v1.5`, Hugging Face, CPU | Normalized **768-dimensional** dense vectors. Revision pinned to `a5beb1e3e68b9ab74eb54cfd186867f64f240e1a`. Query encoding uses the BGE retrieval instruction; indexed documents use the document encoding path. |
| Docling | Running worker: **2.150.1**; `docling-core` **2.107.0** | Converts PDF, DOCX and images into canonical document structure, text, tables, pictures and source locators. This is an extraction pipeline, not the question-answering LLM. |
| Layout detection | Worker default `layout_heron`, `docling-project/docling-layout-heron` | Default inspected model specification uses revision `main`; unlike the embedding encoder, this layout-weight revision is not explicitly pinned to an immutable commit by application configuration. |
| OCR | `RapidOcrOptions(backend="onnxruntime")`; OCR enabled | Handles scanned text through Docling. Exact OCR model-weight revision was not independently audited in this review. |
| Tables | Table-structure extraction enabled; worker default `accurate` mode with cell matching | Exports canonical tables, Markdown and row records. Extracting a table does not establish numeric/financial reasoning accuracy. |
| Chunking | Docling `HybridChunker`, target cap 450 tokens, `merge_peers=False` | Keeps structural boundaries, repeats table headers and emits heading context. Configured Hugging Face tokenizer checks budgets; the cap is not a target minimum chunk length. |
| Retrieval | pgvector cosine distance plus PostgreSQL full-text search, RRF | Four agent search hits can expand into complete relevant subsections and parent introductions. No numeric semantic cutoff, ANN index or cross-encoder reranker is implemented. |

The earlier inspected indexed fixture snapshot contained 60 chunks and 60 matching normalized 768-dimensional vectors. Dimension/profile/hash checks found no vector-integrity defect. That is an integrity result, not proof that every retrieval ranking is optimal. Changing the answer model does not require re-embedding; changing the embedding profile does.

The agent is capped at **five exploration iterations**, with a 1,800-second configured run budget and 300-second LLM request timeout. The optional Codex gateway has a separate 270-second subprocess deadline and accepts one active request per gateway. Its token limit is advisory with a post-completion usage check, not a native hard generation cap. The supported canonical CLI model name in the saved test was `gpt-6.1-sol`; the prefixed `openai/gpt-6.1-sol` probe was rejected. `CODEX_FORCE_AUTH_JSON=1` was supplied, but its effect as an authentication switch was not established.

These are the **local test settings**, not fresh-install defaults: `.env.example` defaults to LM Studio / `qwen/qwen3.5-9b`. Groq / `openai/gpt-oss-120b` remains supported. Earlier LFM/Qwen experiments were development investigations and are archived; they are not interchangeable with the retained Codex benchmark.

### Runtime versions and reproducibility

The running worker reports sentence-transformers 5.7.0, transformers 5.23.0, LangGraph 1.2.22 and Langfuse SDK 4.18.0. The newly consolidated root environment reports sentence-transformers 5.7.0, **transformers 5.17.0**, Docling 2.150.1, LangGraph 1.2.22, Langfuse SDK 4.18.0 and DeepEval 4.3.4. The existing Docker worker was not rebuilt as part of this documentation task; do not assume its installed packages match the newly updated root lockfile byte-for-byte.

Application and evaluation now share the root `.venv`, `.env`, `pyproject.toml` and `uv.lock`. A legacy nested environment was temporarily retained for an evaluation already in progress, per the user's request. It is not the environment used by the new run commands. Confident AI credentials are not required; the evaluation entry point disables cloud credential use in its own process without deleting stored credentials.

Implementation references: [converter](../backend/converter.py), [chunker](../backend/chunking.py), [embeddings](../backend/embeddings.py), [retrieval](../backend/retrieval.py), [agent](../backend/agent.py), [judge](../evaluation/deepeval_suite/judge.py).

## 2. Langfuse dashboard and how it helps

Project: [Document Intelligence](http://localhost:3000/project/opzen-documents).

The local read-only API confirmed:

- Langfuse health is OK; server version **4.80.0**.
- Project ID `opzen-documents`, displayed name `Document Intelligence`.
- Dataset `financial-ui-golden-v1` exists with **30 items**.
- The observations endpoint returns recent `agent.run`, `agent.plan`, `agent.act`, `agent.finish` and `llm.structured` observations. Multiple stages share trace IDs, providing a concrete investigation path.
- The v3 scores endpoint responds successfully. Score population and configured native evaluator rules were **not audited**, so this review does not claim that DeepEval scores already appear in the dashboard.

The browser control host was unavailable, so this was **not a visual inspection of dashboard charts**. The observations API defaults to core/basic fields; absent model/input/output fields in that response do not prove those values are missing from stored telemetry. An expanded-field request returned 400, and a follow-up inspection was not executed because automatic approval review hit an account usage limit. Payload completeness, score counts, usage totals and billing totals remain unverified in this review.

| Dashboard use | How it helps this prototype | Current boundary |
|---|---|---|
| Trace timeline | Follow planning, tool execution, synthesis and model calls; identify the stage that stalled. | Observability explains execution; it does not prove an answer is correct. |
| Stage timing | Locate slow conversion, retrieval, model and verification stages. | No project-wide latency SLA or capacity claim was established from the dashboard. |
| Failure inspection | Correlate a run with failed model/tool stages. Instrumented HTTP failures include safe status categories. | Error level alone is not a semantic-quality score; inspect recorded output/status and the application result. |
| Model usage | Instrumentation records provider/model and token usage when returned by the provider. | Custom model pricing and completeness of current usage/cost records were not verified. Dashboard cost is not total infrastructure cost. |
| Run correlation | Application code records `metadata.run_id`; use it to locate stages belonging to a saved result. | Application run UUIDs and Langfuse trace IDs are different identifiers. Metadata payload availability was not verified in the default API sample. |
| Dataset experiments | The 30 stored examples provide a foundation for repeatable experiments and comparisons. | Publishing dataset items alone does not link every application result to a dataset experiment. That experiment/evaluation integration is not implemented in the repo. |
| Scores and review | Langfuse can display imported metric values and judge explanations alongside traces. | Saved DeepEval scores are not automatically imported; general feedback/annotation integration is also incomplete. |

Current `.env` sets **`LANGFUSE_CAPTURE_CONTENT=false`**. [Telemetry code](../backend/telemetry.py) therefore summarizes protected payload fields instead of exporting full questions, document bodies and answers. Explicit stage/status metadata still helps debugging. Opt-in capture is bounded to 24,000 serialized characters with common credential/reasoning-field redaction; this is not a guarantee that every sensitive business fact is removed.

For native LLM-as-judge evaluation, Langfuse needs actual question, final answer, retrieved context and, for reference-based metrics, the expected answer. A content summary is insufficient. An alternative is importing already computed DeepEval scores, which needs no additional judge calls. Neither path was enabled during this review. The restricted Codex gateway has not been validated against Langfuse's native evaluator request format.

Official feature references: [Langfuse LLM-as-judge](https://langfuse.com/docs/evaluation/evaluation-methods/llm-as-a-judge), [scores via SDK](https://langfuse.com/docs/evaluation/evaluation-methods/scores-via-sdk).

## 3. Current offerings and limits

| Area | Offered now | Limitation |
|---|---|---|
| Ingestion | Authenticated upload, idempotency, immutable revisions, PDF batches, Docling artifacts and processing activity. | Manual intake; legacy `.doc` and other unsupported formats need prior conversion. No production connectors. |
| Search and citations | Authorized vector/keyword retrieval, date/revision scopes, canonical subsection expansion and exact source quotes. | No reranker or calibrated relevance cutoff. Exact quote matching does not prove completeness or correct interpretation. |
| Agentic investigation | Metadata/outline exploration, evidence-dependent hops, clarification, follow-ups and persisted bounded LangGraph runs. | Five iterations and finite context can leave gaps; fact coverage and action-argument repair need improvement. |
| Comparison/conflicts | Two selected revisions, deterministic reading-order diff, cited conflict candidates and report-level review. | Whole-revision context can be noisy; not full semantic clause alignment or legal precedence resolution. |
| Daily updates | Content/processing revisions, conservative freshness invalidation and repeated permission/scope checks. | Targeted incremental invalidation and production change feeds are future work. |
| Security and feedback | Server-side demo identity keys, document permissions, source reauthorization, audit and Confirm/Dismiss/Unresolved conflict review. | Real SSO and department-policy integration are mocked; no general answer-rating/correction loop. |
| Images/tables | OCR, table/picture extraction, previews and provenance. | No vision-LLM/image-embedding question-answering pipeline or Pandas/code execution. OCR/table correctness is not comprehensively benchmarked. |
| Scale | Durable storage, leases, checkpoints and explicit processing profiles. | Exact SQL search and bounded scopes (up to 100 revisions) need redesign/load testing for one million documents. |

The scored corpus has five native-text financial PDFs, not a broad real-world contract/regulatory corpus. Word and scanned-PDF examples can be generated for demonstrations, but the financial golden-set scores do not validate those formats. The local [performance smoke test](performance-and-cost.md) measures retrieval/API reads; it does not establish ingestion throughput, answer latency, production concurrency or actual billed cost.

## 4. What was evaluated

The 30-case set covers direct lookup, policy thresholds, historical scope, cross-page subsection completion, named-authority multi-hop questions, follow-ups, two-document comparisons, true/false conflicts, unresolved precedence, missing information, clarification, restricted access, quoted prompt injection and an exact threshold boundary. It contains **55 source-verified references** with frozen effective date **2026-11-15**.

| Evaluation side | Current evidence/status |
|---|---|
| Dataset integrity | Five original three-page PDF hashes, case schemas, permission/scope references and 55 quotes validated offline. |
| Application regression tests | Latest verification: **24 passed; 16 integration tests skipped** by their environment gate. Mocked-model tests verify control flow, not real-model reasoning. |
| Evaluator regression tests | **12 passed** after shared-env/root-config changes; mocked HTTP and configuration checks, no new judge calls. |
| UI replay | **64/64 rendering checks passed** across 29 saved outcomes. The expected no-source API rejection has no saved answer view. Rendering is separate from quality. |
| Retained application run | All 30 outcomes collected; **28/30 automatic diagnostics passed**, with no remaining provider-blocked application outcomes after documented retries. |
| Retained DeepEval run | All 30 cases attempted; **29/30 application-integrity passes**, **69 judge-metric errors across 20 cases**. Full semantic coverage was not achieved. |
| Langfuse evaluation | Dataset verified; stage traces verified. No automatic DeepEval score import or dataset-experiment linkage implemented. Native evaluator configuration/score population was not inspected. |

Automatic diagnostics include indicative fact terms, exact citations, minimum cited-document counts, allowed source scope, prohibited output strings, nonempty required answers and the exploration cap. They do not establish semantic entailment. Human or independent-model review remains necessary.

### Completed 30-case DeepEval first pass

| Metric | Threshold | Mean of valid scores | Passed/scored | Judge errors | Not applicable |
|---|---:|---:|---:|---:|---:|
| Application integrity | 1.0 | 0.967 | 29/30 | 0 | 0 |
| Reference correctness (G-Eval) | 0.7 | 0.900 | 15/16 | 14 | 0 |
| Answer Relevancy | 0.7 | 0.971 | 16/17 | 12 | 1 |
| Faithfulness | 0.8 | 1.000 | 13/13 | 14 | 3 |
| Contextual Precision | 0.7 | 0.527 | 3/16 | 7 | 7 |
| Contextual Recall | 0.7 | 0.953 | 15/16 | 7 | 7 |
| Contextual Relevancy | 0.7 | 0.417 | 2/13 | 15 | 2 |

These means exclude errors and inapplicable metrics. In particular, faithfulness 1.0 on 13 scored cases is **not** faithfulness 1.0 on all 30. Interrupted later retries were archived and are not merged into this table. See the retained [summary](../evaluation/deepeval_suite/results/codex-gpt-6.1-sol-30/summary.json) and [per-case records](../evaluation/deepeval_suite/results/codex-gpt-6.1-sol-30/results.jsonl).

### Restored LFM baseline

The historical `lfm3-omni-2b` run is restored under `evaluation/deepeval_suite/results/20261115T134006Z/`, alongside its original application prediction file. It evaluated application integrity, reference correctness and faithfulness only. See [the LFM versus GPT-SOL findings](lfm-vs-gpt-sol-findings.md) for metric denominators, concrete failures and comparison caveats. The original archived copy and raw scores are unchanged.

## 5. Interpreting the findings

1. **Supported is not necessarily complete.** FIN-011 has faithfulness 1.0 but correctness 0.6: it denies a retention waiver but omits the seven-year minimum and second required citation.
2. **Lexical diagnostics can falsely fail.** FIN-030 states the correct equivalent expiry date in words; the ISO-date term check fails, while reference correctness is 1.0.
3. **Context packing needs work.** The retained 30-case run delivered 480 passages across 28 nonempty contexts (median 11, maximum 48). Final evidence is serialized by evidence ID; comparison loads entire selected revisions. Context precision therefore measures that delivered order, not raw BGE/RRF ranking. Low precision/relevance is not sufficient evidence to replace the embedding model.
4. **Reference design affects recall scores.** FIN-027's reference includes an application security expectation absent from document evidence, reducing context recall despite retrieving the correct name. FIN-028 is a correct no-source API rejection whose generic harness message scores Answer Relevancy 0.5. Keep these measurement caveats visible.
5. **Execution errors are not quality failures.** Provider deadlines/usage limits must remain separate from wrong answers and from honest abstentions. No extra retries, scoring changes or model fixes were made for this document.

The next priorities are independent reviewed evaluation, required-fact coverage before sufficient stops, relevance-preserving context packing, durable score/experiment linkage in Langfuse, production identity and capacity testing. The current system is a working evidence-oriented prototype with observable, reproducible limits—not a certified financial-policy authority.

Related documents: [evaluation report](evaluation-results.md), [manual run commands](../evaluation/deepeval_suite/RUNNING.md), [demo guide](demo.md), [architecture](design/architecture.md), [database design](design/database.md).

Evaluation coverage and planned automation: [automation plan](evaluation-automation-plan.md). MRR, nDCG and aggregated operational SLO/cost gates are proposed, not already implemented.
