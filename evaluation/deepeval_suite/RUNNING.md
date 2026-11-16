# Run DeepEval yourself

The evaluation jobs and temporary gateways were stopped on request. Original results remain intact. A local Codex application gateway was left running at cleanup time; check its health before use. For LM Studio judges, start its server and use the configured port 1234 instead.

## 1. Install the shared root environment

Run from the repository root:

```bash
cd /path/to/opzen_
uv sync --frozen
```

The suite loads only the repository-root `.env`. Nested `.env` files, `.env.local` files and DeepEval legacy keyfiles are not loaded; explicit shell variables still override root values. Both the application and evaluation use the root `.venv`, `pyproject.toml` and `uv.lock`. There is no nested environment. The preserved predictions were generated with `gpt-6.1-sol`; choose the judge using `DEEPEVAL_JUDGE_MODEL`, `DEEPEVAL_JUDGE_BASE_URL` and related settings. The gateway steps below apply when choosing the optional Codex judge. Credentials stay in `.env`; do not put them into commands.

## 2. Check the judge gateway

```bash
curl -fsS http://127.0.0.1:1235/health
```

If it is unavailable, start it in a separate terminal and leave that terminal open:

```bash
cd /path/to/opzen_
uv run python -m uvicorn scripts.codex_gateway:app \
  --host 127.0.0.1 --port 1235 --no-access-log
```

This uses the host CLI's existing login. Saved-prediction evaluation does not require Docker API/worker/MinIO services and does not generate new application answers.

## 3. Run all 30 saved answers

No Confident AI API key, login, `--project` flag or virtual-environment override is needed. The suite runs locally and ignores cloud credentials in its process without changing saved credentials.

```bash
uv run python -m evaluation.deepeval_suite.run \
  --predictions evaluation/financial-v1/results-codex-gpt-6.1-sol-20261116-30.jsonl \
  --max-cases 30
```

The default includes Answer Relevancy, Faithfulness, Contextual Precision, Contextual Recall and Contextual Relevancy, plus reference correctness and application integrity. Inapplicable metrics remain marked separately. Existing thresholds and the root timeout settings are used unchanged.

For a one-case check, replace `--max-cases 30` with `--ids FIN-001 --max-cases 1`.

For deterministic checks without any judge calls:

```bash
uv run python -m evaluation.deepeval_suite.run \
  --predictions evaluation/financial-v1/results-codex-gpt-6.1-sol-20261116-30.jsonl \
  --max-cases 30 --integrity-only
```

## 4. Read the results

Each invocation prints a new output directory under `evaluation/deepeval_suite/results/`:

- `results.jsonl`: written after each completed case; includes scores, reasons and errors.
- `summary.json`: written when the selected run finishes; includes per-metric means and separate error/not-applicable counts.

Read the completed first pass:

```bash
python3 -m json.tool evaluation/deepeval_suite/results/codex-gpt-6.1-sol-30/summary.json
```

That first pass covered 30 cases, with 69 judge-metric errors across 20 cases. Subsequent retry directories were partial when stopped and have been moved to the ignored local submission archive. Do not treat missing retry summaries as completed evaluations. No combined final retry scorecard was produced.

## Optional: parallel execution

Each gateway accepts only one active request. Use a different gateway for each parallel worker; sharing port 1235 produces busy errors. Model/account limits still apply.

Start these in two separate terminals:

```bash
uv run python -m uvicorn scripts.codex_gateway:app --host 127.0.0.1 --port 1236 --no-access-log
```

```bash
uv run python -m uvicorn scripts.codex_gateway:app --host 127.0.0.1 --port 1237 --no-access-log
```

After both are ready, run the following from the repository root in a third terminal. These batches cover all 30 cases without overlapping IDs:

```bash
DEEPEVAL_JUDGE_BASE_URL=http://127.0.0.1:1236/v1 \
uv run python -m evaluation.deepeval_suite.run \
  --predictions evaluation/financial-v1/results-codex-gpt-6.1-sol-20261116-30.jsonl \
  --ids FIN-001,FIN-003,FIN-005,FIN-007,FIN-009,FIN-011,FIN-013,FIN-015,FIN-017,FIN-019,FIN-021,FIN-023,FIN-025,FIN-027,FIN-029 \
  --output /tmp/opzen-eval-odd-01 &

DEEPEVAL_JUDGE_BASE_URL=http://127.0.0.1:1237/v1 \
uv run python -m evaluation.deepeval_suite.run \
  --predictions evaluation/financial-v1/results-codex-gpt-6.1-sol-20261116-30.jsonl \
  --ids FIN-002,FIN-004,FIN-006,FIN-008,FIN-010,FIN-012,FIN-014,FIN-016,FIN-018,FIN-020,FIN-022,FIN-024,FIN-026,FIN-028,FIN-030 \
  --output /tmp/opzen-eval-even-01 &

wait
```

The separate `--output` paths prevent timestamp-directory collisions. Each path must be new; change the suffix before rerunning. Close both temporary gateway terminals with Ctrl+C when finished. To stop evaluations, interrupt each evaluation process explicitly; stopping a shell's `wait` alone can leave background jobs running.

For optional execution-error-only retries, see [README.md](README.md). Valid low scores are not selected by that option.
