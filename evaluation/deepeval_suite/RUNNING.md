# Run and inspect evaluation

Run from the repository root with `uv sync --frozen`. The application and evaluator share the root environment and `.env`. No Confident AI key is needed.

```bash
# Collect new application outputs only when explicitly wanted.
uv run python -m scripts.evaluate_golden --run --max-cases 30 --pause 0

# Score the existing predictions in a new, detached experiment.
uv run --frozen python -m evaluation.deepeval_suite.background start \
  --predictions evaluation/financial-v1/results-20261116-175816.jsonl \
  --output evaluation/deepeval_suite/results/my-new-experiment --max-cases 30

# Inspect the consolidated latest results without model calls.
python3 -m json.tool evaluation/deepeval_suite/results/chunking-v2-durable-20261116-1/summary.json
```

Use a new output name for a fresh experiment; use the same output with `background start` for compatible checkpoint resume. Read [automation/durability commands and roadmap](../../docs/evaluation-automation-plan.md) and [the latest scorecard](../../docs/evaluation-results.md) before interpreting means or retrying exhausted failures. The judge endpoint must be available for semantic scoring; saving predictions alone does not compute semantic metrics. The local Codex gateway is optional and does not provide a hard upstream token cap.
