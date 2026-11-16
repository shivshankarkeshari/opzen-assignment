# DeepEval suite

DeepEval 4.3.4 runs in the same repository-root `.venv` as the application, using the root `pyproject.toml` and `uv.lock`. It scores saved application outputs; it does not generate replacement answers. Follow [RUNNING.md](RUNNING.md) for install/run/parallel commands. See [the result report](../../docs/evaluation-results.md) for the completed first pass and limitations.

## Run against saved predictions

Run these commands from the repository root. Install the shared root dependencies:

```bash
uv sync --frozen
```

If you have already collected application outputs with:

```bash
uv run python -m scripts.evaluate_golden --run --max-cases 5 --pause 0
```

pass the resulting prediction file to DeepEval. For five cases from the retained application run:

```bash
uv run python \
  -m evaluation.deepeval_suite.run \
  --predictions evaluation/financial-v1/results-codex-gpt-6.1-sol-20261116-30.jsonl \
  --max-cases 5 \
  --output evaluation/deepeval_suite/results/my-evaluation-run
```

The configured judge endpoint must be running. This uses judge settings from the root `.env` and calls the judge model; it does not submit the questions to the application again. All five RAG metrics below, reference correctness and application integrity are included by default. No virtual-environment override or Confident AI key is required.

Replace `--predictions` with your own saved JSONL file. Use a **new output directory** for every run; an existing directory is rejected to preserve previous results. To score a saved 30-case run, change `--max-cases` to `30` and supply its prediction file.

The output directory contains:

- `results.jsonl`: per-case scores, judge reasons and execution errors, written after each completed case.
- `summary.json`: aggregate metric scores, pass/fail counts, errors and not-applicable counts, written when the selected run finishes.

View the example run's summary after completion:

```bash
python3 -m json.tool evaluation/deepeval_suite/results/my-evaluation-run/summary.json
```

Application collection alone saves responses and automatic diagnostics, not these semantic metric scores. The saved generated answer is at `response.result.claims[].text` in the prediction JSONL. Stopping DeepEval early preserves completed-case rows but may leave no final summary.

## Metrics and configuration

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

The repository-root `.env` is the only configuration file loaded by the suite, regardless of the working directory. DeepEval's automatic `.env`/`.env.local` discovery and legacy keyfile loading are disabled before SDK imports. Explicit process environment variables take precedence, including per-worker endpoints. There is no separate evaluation environment or project configuration.

Root settings configure `DEEPEVAL_JUDGE_MODEL`, base URL, API key, reasoning effort and finite request/time budgets independently from application generation. `DEEPEVAL_PER_TASK_TIMEOUT_SECONDS_OVERRIDE=2100` exceeds the selected 1800-second judge budget; startup rejects a mismatch. The judge reserves a complete request allowance before starting a call. There are no automatic adapter retries.

`--retry-errors path/to/results.jsonl` selects only prior execution errors, not valid low scores, and writes a separate attempt. Prior scored metrics are marked `previously_scored` in retry-only summaries. Do not treat a partial retry as a full-set result. No retry is started automatically.

Results stay local. The suite ignores Confident AI credentials in its process without changing saved logins or env files; no Confident AI key is required. Sending scores to Langfuse or enabling Confident AI is not implemented. Judge HTTP errors retain safe status/categories without private response bodies.

Regression checks use mocked HTTP, make no model requests and run from the repository root:

```bash
uv run python -m unittest evaluation.deepeval_suite.test_suite
```

## Restored historical baseline and automation plan

The original LFM run is available in [results/20261115T134006Z/](results/20261115T134006Z/summary.json). Its application predictions are retained with matching SHA-256 provenance. It evaluated three metrics only. See [model comparison findings](../../docs/lfm-vs-gpt-sol-findings.md) and the [evaluation automation plan](../../docs/evaluation-automation-plan.md) for current coverage, missing ranking/monitoring metrics and proposed regression gates.
