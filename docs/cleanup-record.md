# Submission cleanup

Experiment history and the previous README were preserved locally under `.submission-archive/` with SHA-256 checksums. That directory is excluded from submission. Only regenerable caches, bytecode and PDF preview images were deleted. Local credentials, virtual environments, application storage and Docker volumes were preserved.

The five original PDF fixtures and golden labels were retained unchanged. Completed Codex predictions, their provenance manifest, the completed first-pass DeepEval results, and the 64-check UI report remain available. Interrupted retries, older model runs and investigation notes are archived, not presented as completed results. No evaluation logic or application source was changed by this cleanup.

Verification after cleanup: Ruff passed; application suite 24 passed/16 integration tests skipped; isolated evaluator suite 11 passed; offline validation confirmed all five original three-page PDF hashes and 55 evidence references; 12 submitted Markdown files had no broken local file links. Five read-only requests per API operation produced the recorded performance smoke measurements. No LLM generation/evaluation calls were made.

Follow-up requested cleanup: two local five-case summary/judge directories and four partial application prediction files were deleted from active evaluation folders. Supporting partial-batch records were archived locally; their full consolidated 30-case output remains unchanged. The original LFM 30-case evaluation and its matching predictions were restored byte-for-byte as a second historical baseline. No partial retry is represented as a completed benchmark.
