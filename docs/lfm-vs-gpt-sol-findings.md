# LFM and GPT-SOL evaluation findings

The historical **LM Studio / `lfm3-omni-2b`** evaluation is restored to [20261115T134006Z](../evaluation/deepeval_suite/results/20261115T134006Z/summary.json), with its original [case records](../evaluation/deepeval_suite/results/20261115T134006Z/results.jsonl) and [30 application predictions](../evaluation/financial-v1/results-20261115-183958.jsonl). The restored files match their archived bytes; the prediction and golden hashes match the original summary. The archived copy is preserved.

The comparison below describes historical runs, not a controlled model-ranking experiment. Generation and judge models, runtime/output budgets and diagnostic versions differ. The LFM run measured **three metrics**, while the later GPT-SOL run measured seven. A metric not run is **not measured**, not zero. Neither run is a production accuracy estimate.

## Results with the correct denominators

| Observation | LFM3-Omni-2B | GPT-SOL retained run |
|---|---:|---:|
| Application cases | 30 | 30 consolidated outcomes |
| Automatic application diagnostics | 5/30 | 28/30 |
| Application-integrity metric | 7/30 | 29/30 |
| Reference correctness | 9/27 scored passed; mean 0.322 | 15/16 successfully scored passed; mean 0.900; 14 judge errors |
| Faithfulness | 2/2 scored passed; 28 not applicable | 13/13 successfully scored passed; 14 errors, 3 not applicable |
| Answer Relevancy and three context metrics | Not run | Run; see the full result report |
| Judge execution errors | 0 | 69 metric errors across 20 cases |

Application term diagnostics and the application-integrity metric are different checks. Integrity excludes lexical fact-term coverage; historical diagnostic behavior also changed. Their pass counts must not be treated as interchangeable.

LFM's two faithful scored answers do not mean all 30 answers were faithful. Most cases had no substantive final answer, so faithfulness was not applicable. Similarly, GPT-SOL's mean correctness of 0.900 describes only 16 usable judgments. The GPT run has much stronger answer evidence but incomplete judge coverage. LFM's lack of execution errors does not establish that its judge decisions are reliable.

The GPT application file was consolidated from development batches with seven provider-only retries. Partial source batches are no longer included as separate submission benchmarks. The unchanged consolidated rows preserve historical provenance labels; their supporting records are archived locally. No new run was made for this comparison.

## Why LFM scored poorly in these runs

**1. Answerable cases often ended without claims.** Saved LFM outputs contain evidence in 22/30 cases but nonempty final claims in only 2/30. Twenty-six cases stopped with `no_progress`; two had model failures, one follow-up was dependency-blocked, and one correctly had no eligible source. Not every question should produce a claim, but many answerable questions failed. These are observable pipeline failures, not merely strict judging.

**2. Tool arguments and controller behavior interacted badly.** The earlier read-only checkpoint investigation recorded document IDs copied into `version_id` for cases such as FIN-004/011/015/016/029. The controller correctly rejected unauthorized/invalid revision identifiers, but remembered failed actions and stopped on repeats. The action schema's free-form IDs and limited repair path increase the burden on the model. This is partly a tool-contract/controller weakness; parameter count alone is not a sufficient diagnosis.

**3. Finding evidence did not ensure a surviving answer.** The earlier diagnostic inspection found all required quoted facts in the correct document/page evidence for nine answerable cases, but eight of those returned no final claims. Saved outputs record exact-quote/paired-source rejection in 16 cases and semantic removal in two; these flags overlap. The pipeline uses the configured model for generation and semantic verification, so a weak verifier can remove a good draft or miss a bad one. Rejected drafts were not retained comprehensively enough to isolate every rejection's cause.

**4. Cross-document coverage was incomplete.** FIN-012 and FIN-014 collected only the older policy side in the LFM run. The comparison tool exists, but the model did not reliably choose the operation that loads both revisions. Source-pair validation then prevents an unsupported comparison, without automatically repairing the missing side. GPT-SOL's retained answers correctly distinguish the two versions and the true/false conflict scenarios.

**5. Context presentation adds unnecessary difficulty.** Paragraph-sized indexed chunks are expanded into canonical items, including headings. Final evidence is serialized by evidence ID instead of relevance/reading order. A small model must reconstruct scope, facts, exception conditions and citation IDs across that fragmented input. This is a plausible contributor; no isolated experiment quantified its causal effect.

**6. The LFM judge also made identifiable mistakes.** The archived analysis found FIN-022 scored correctness 0.0 for correctly stating that no current bank balance was supplied, while FIN-015 received a passing 0.7 despite having no substantive conflict claim. A low model-judged score can therefore combine application mistakes with judge mistakes. Independent review is needed to distinguish them.

## What the evidence does not establish

- It does not establish that 60–90-token chunks are inherently wrong. The 450-token setting is a maximum; complete short paragraphs can be useful retrieval units. Subsection expansion retrieved cross-page approval conditions in existing evidence.
- It does not establish an embedding defect: the inspected snapshot had 60 matching normalized 768-dimensional BGE vectors with consistent profile/input hashes. Eight failures already contained the required facts. Semantic ranking can still improve.
- It does not establish that larger context is always better. Whole-revision comparison produces up to 48 passages, and GPT-SOL also has low context precision/relevance in many cases.
- It does not establish all GPT outputs are correct. FIN-011 omits a required minimum/citation, FIN-030 exposes a lexical date-format false failure, and its judge run has substantial provider errors.
- It does not establish that changing only model size would reproduce the observed difference. Use the same frozen corpus, prompts, tool schema, budgets and independent judge for a future controlled comparison.

Priority hypotheses to test are constrained tool IDs and bounded repair, explicit fact/document-side coverage before stopping, relevance-preserving evidence packing, and judge calibration. Do not relax exact citation validation merely to raise pass counts. See [evaluation results](evaluation-results.md) and the [automation plan](evaluation-automation-plan.md).
