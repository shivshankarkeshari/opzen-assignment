"""Deterministic execution/integrity checks, separate from semantic judging."""

from deepeval.metrics import BaseMetric


class ApplicationIntegrity(BaseMetric):
    threshold = 1.0
    async_mode = False
    verbose_mode = False
    evaluation_model = "deterministic"

    @property
    def __name__(self):
        return "Application integrity"

    def measure(self, test_case, *args, **kwargs):
        diagnostic = test_case.metadata["diagnostic"]
        outcome = diagnostic["outcome"]
        checks = diagnostic.get("checks", {})
        required = (
            "exact_quotes",
            "minimum_cited_documents",
            "scope",
            "no_forbidden_answer_strings",
            "bounded_iterations",
        )
        failures = [key for key in required if not checks.get(key)] if outcome == "scored" else []
        if outcome == "scored" and test_case.metadata["answerability"] == "answerable":
            if not checks.get("nonempty_answer"):
                failures.append("nonempty_answer")
        valid_execution = outcome in ("scored", "clarification", "no_eligible_source")
        if outcome in ("clarification", "no_eligible_source"):
            valid_execution = diagnostic["automatic_checks_passed"]
        if not valid_execution:
            failures.insert(0, outcome)
        self.score = float(not failures)
        self.success = self.score >= self.threshold
        self.reason = (
            "Failed: " + ", ".join(failures)
            if failures
            else "Execution and stored citation/access/budget checks passed; semantic correctness is separate."
        )
        return self.score

    async def a_measure(self, test_case, *args, **kwargs):
        return self.measure(test_case, *args, **kwargs)

    def is_successful(self):
        return self.success is True and self.error is None
