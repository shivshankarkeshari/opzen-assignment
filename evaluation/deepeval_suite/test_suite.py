"""Regression checks for misleading evaluation passes; no model/server needed."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import httpx
from pydantic import BaseModel

from .judge import LocalJudge
from .metrics import ApplicationIntegrity
from .run import error_metric_names, make_case, metric_summary, run


class EvaluationSafetyTests(unittest.TestCase):
    def setUp(self):
        path = Path(__file__).resolve().parents[1] / "financial-v1/golden.jsonl"
        self.golden = json.loads(path.read_text().splitlines()[0])

    def test_only_root_env_is_loaded_from_a_nested_working_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            package = root / "evaluation" / "deepeval_suite"
            package.mkdir(parents=True)
            initializer = Path(__file__).with_name("__init__.py").read_text()
            (package / "__init__.py").write_text(initializer)
            (root / ".env").write_text("OPZEN_ROOT_CONFIG=loaded\nOPZEN_PROCESS_CONFIG=file\n")
            for name in (".env", ".env.local"):
                (package / name).write_text("OPZEN_AUTO_CONFIG=unexpected\n")
            env = dict(os.environ)
            for key in (
                "OPZEN_ROOT_CONFIG",
                "OPZEN_AUTO_CONFIG",
                "DEEPEVAL_DISABLE_DOTENV",
                "DEEPEVAL_DISABLE_LEGACY_KEYFILE",
            ):
                env.pop(key, None)
            env.update(
                PYTHONPATH=str(root),
                CONFIDENT_API_KEY="test-inherited-cloud-key",
                OPZEN_PROCESS_CONFIG="kept",
            )
            result = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    "import os; import evaluation.deepeval_suite; "
                    "from deepeval.config.settings import get_settings; get_settings(); "
                    "assert os.environ['OPZEN_ROOT_CONFIG'] == 'loaded'; "
                    "assert 'OPZEN_AUTO_CONFIG' not in os.environ; "
                    "assert os.environ['OPZEN_PROCESS_CONFIG'] == 'kept'; "
                    "assert os.environ['DEEPEVAL_DISABLE_LEGACY_KEYFILE'] == '1'; "
                    "from deepeval.confident.api import is_confident; assert not is_confident()",
                ],
                cwd=package,
                env=env,
                capture_output=True,
                text=True,
                timeout=30,
            )
            self.assertEqual(result.returncode, 0, "Root-only environment loading failed")

    def test_provider_failure_is_not_a_correct_abstention(self):
        case = make_case(
            self.golden,
            {"score": {"outcome": "provider_blocked", "automatic_checks_passed": False}},
        )
        metric = ApplicationIntegrity()
        self.assertEqual(metric.measure(case), 0)
        self.assertFalse(metric.is_successful())

    def test_empty_answer_with_right_terms_in_gaps_cannot_pass_integrity(self):
        case = make_case(
            self.golden,
            {
                "score": {"outcome": "scored", "checks": {"nonempty_answer": False}},
                "response": {"result": {"claims": [], "gaps": ["seven years"]}},
            },
        )
        self.assertEqual(ApplicationIntegrity().measure(case), 0)

    def test_retrieval_context_comes_only_from_actual_evidence(self):
        case = make_case(
            self.golden,
            {
                "score": {"outcome": "scored"},
                "response": {"result": {"evidence": {"e1": {"text": "Actual retrieved passage"}}}},
            },
        )
        self.assertEqual(len(case.retrieval_context), 1)
        self.assertIn("Actual retrieved passage", case.retrieval_context[0])
        self.assertNotIn("seven years", case.retrieval_context[0])

    def test_judge_rejects_truncated_json_instead_of_scoring(self):
        response = httpx.Response(
            200,
            request=httpx.Request("POST", "http://local"),
            json={"choices": [{"finish_reason": "length", "message": {"content": "{}"}}]},
        )
        with patch("evaluation.deepeval_suite.judge.httpx.post", return_value=response):
            with self.assertRaisesRegex(ValueError, "truncated"):
                LocalJudge().generate("evaluate")

    def test_custom_judge_returns_requested_schema(self):
        class Verdict(BaseModel):
            score: int

        response = httpx.Response(
            200,
            request=httpx.Request("POST", "http://local"),
            json={"choices": [{"finish_reason": "stop", "message": {"content": '{"score": 3}'}}]},
        )
        with patch("evaluation.deepeval_suite.judge.httpx.post", return_value=response) as call:
            result = LocalJudge().generate("evaluate", schema=Verdict)
            self.assertIsInstance(result, Verdict)
            self.assertEqual(result.score, 3)
            self.assertEqual(
                call.call_args.kwargs["json"]["response_format"]["type"], "json_schema"
            )

    def test_context_order_is_preserved_without_sorting_by_gold_relevance(self):
        case = make_case(
            self.golden,
            {
                "score": {"outcome": "scored"},
                "response": {
                    "result": {
                        "evidence": {
                            "z": {"text": "First actual passage"},
                            "a": {"text": "Second actual passage"},
                        }
                    }
                },
            },
        )
        self.assertIn("First actual passage", case.retrieval_context[0])
        self.assertIn("Second actual passage", case.retrieval_context[1])

    def test_summary_separates_judge_errors_blocked_cases_and_missing_context(self):
        name = "Answer Relevancy"
        rows = [
            {"metric_applicability": {name: state}, "evaluation": {"metrics_data": metrics}}
            for state, metrics in [
                ("evaluated", [{"name": name, "score": 0.8, "success": True, "error": None}]),
                (
                    "evaluated",
                    [{"name": name, "score": None, "success": False, "error": "timeout"}],
                ),
                ("blocked", []),
                ("not_applicable", []),
            ]
        ]
        result = metric_summary(rows)[name]
        self.assertEqual(result["mean_score"], 0.8)
        for field in ("passed", "scored", "errors", "blocked", "not_applicable"):
            self.assertEqual(result[field], 1)

    def test_framework_timeout_mismatch_fails_before_reading_data_or_calling_model(self):
        with (
            patch("evaluation.deepeval_suite.run.is_confident", return_value=False),
            patch(
                "evaluation.deepeval_suite.run.get_settings",
                return_value=SimpleNamespace(
                    DEEPEVAL_PER_TASK_TIMEOUT_SECONDS=180,
                ),
            ),
            patch(
                "evaluation.deepeval_suite.run.LocalJudge",
                return_value=SimpleNamespace(timeout_seconds=1800),
            ),
        ):
            with self.assertRaisesRegex(ValueError, "task timeout"):
                run(SimpleNamespace(integrity_only=False))

    def test_judge_reserves_full_request_budget_before_starting_http_call(self):
        judge = LocalJudge()
        judge.deadline = 105
        judge.request_timeout_seconds = 30
        with (
            patch("evaluation.deepeval_suite.judge.time.monotonic", return_value=100),
            patch("evaluation.deepeval_suite.judge.httpx.post") as post,
        ):
            with self.assertRaisesRegex(RuntimeError, "budget"):
                judge.generate("evaluate")
            post.assert_not_called()
        self.assertEqual(judge.requests, 0)

    def test_retry_selects_execution_errors_without_retrying_valid_low_scores(self):
        row = {
            "evaluation": {
                "metrics_data": [
                    {"name": "low score", "score": 0.1, "success": False, "error": None},
                    {"name": "timeout", "score": None, "success": False, "error": "deadline"},
                ]
            }
        }
        self.assertEqual(error_metric_names(row), {"timeout"})

    def test_judge_error_keeps_category_without_private_response_body(self):
        response = httpx.Response(
            502,
            request=httpx.Request("POST", "http://local"),
            json={
                "detail": "Codex reported output usage above the requested response budget private text"
            },
        )
        with patch("evaluation.deepeval_suite.judge.httpx.post", return_value=response):
            with self.assertRaisesRegex(
                RuntimeError, "Judge HTTP 502: output_budget_exceeded"
            ) as error:
                LocalJudge().generate("evaluate")
            self.assertNotIn("private text", str(error.exception))


if __name__ == "__main__":
    unittest.main()
