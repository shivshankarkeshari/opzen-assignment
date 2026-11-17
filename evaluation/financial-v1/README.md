# Financial golden dataset

This frozen synthetic regression set contains 30 cases and 55 PDF-verified source references across five three-page PDFs. `golden.jsonl` contains scope, actor, expected answerability, required facts, source quotes and prohibited claims. `golden.csv` is a review sheet; `golden.schema.json` defines the case schema. `manifest.json` freezes PDF hashes and reference date **2026-11-15**. Labels were curated independently of generated answers.

From the repository root, validate without model calls:

```bash
uv run python -m scripts.evaluate_golden
```

On a fresh running app, load the bundled PDFs with `uv run python -m scripts.load_financial_test_docs`. This generates local `output/pdf/financial-ui-tests/receipts.json` IDs. Live case collection is opt-in:

```bash
uv run python -m scripts.evaluate_golden --run --max-cases 30 --pause 0
```

Live collection maps current-policy cases to the frozen effective date; historical and selected-version scopes remain explicit. A follow-up includes its parent in the case cap. Reference labels must never be supplied to the application generator or indexed as source documents.

The current saved application outputs are `results-20261116-175816.jsonl` (30 cases). The consolidated scorecard and its source/protocol limitations are linked below. Raw labels remain separate from generation and must not be indexed as evidence.

See [results and limitations](../../docs/evaluation-results.md), [demo steps](../../docs/demo.md) and [DeepEval commands](../deepeval_suite/RUNNING.md). Older model runs and interrupted attempts were preserved in the ignored local submission archive. Langfuse publishing is opt-in with `--publish-langfuse`; dataset IDs are local installation artifacts.
