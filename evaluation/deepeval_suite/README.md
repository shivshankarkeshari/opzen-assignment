# DeepEval suite

An isolated Python project pinned to DeepEval 4.3.4. It scores saved application outputs; it does not generate replacement answers. Follow [RUNNING.md](RUNNING.md) for install/run/parallel commands. See [the result report](../../docs/evaluation-results.md) for the completed first pass and limitations.

| Metric | Threshold |
|---|---:|
| Application integrity | 1.0 |
| Reference correctness (G-Eval) | 0.7 |
| Answer Relevancy | 0.7 |
| Faithfulness | 0.8 |
| Contextual Precision | 0.7 |
| Contextual Recall | 0.7 |
| Contextual Relevancy | 0.7 |

Faithfulness requires claims and actual retrieved evidence; empty answers do not receive a pass. Reference-based context precision/recall require answerable/partial cases with source-grounded expected facts and actual context. Errors, blocked cases and not-applicable metrics are counted separately. Actual final synthesis evidence order is preserved; it is not raw vector rank. The judge is fallible and same-model judging is not independent acceptance evidence.

The root `.env` configures `DEEPEVAL_JUDGE_MODEL`, base URL, API key, reasoning effort and finite request/time budgets independently from application generation. `DEEPEVAL_PER_TASK_TIMEOUT_SECONDS_OVERRIDE=2100` exceeds the selected 1800-second judge budget; startup rejects a mismatch. The judge reserves a complete request allowance before starting a call. There are no automatic adapter retries.

`--retry-errors path/to/results.jsonl` selects only prior execution errors, not valid low scores, and writes a separate attempt. Prior scored metrics are marked `previously_scored` in retry-only summaries. Do not treat a partial retry as a full-set result. No retry is started automatically.

Results stay local. The runner rejects detected Confident AI credentials to prevent unintended uploads; the commands in RUNNING.md temporarily disable login detection without deleting credentials. Sending scores to Langfuse or enabling Confident AI is not implemented. Judge HTTP errors retain safe status/categories without private response bodies.

Regression checks use mocked HTTP, make no model requests and run from the repository root:

```bash
evaluation/deepeval_suite/.venv/bin/python -m unittest evaluation.deepeval_suite.test_suite
```
