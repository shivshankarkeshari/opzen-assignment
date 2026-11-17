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
from deepeval.test_case import LLMTestCase
from pydantic import BaseModel

from .batched_metrics import (
    AnswerVerdicts,
    BatchedContextualRelevancy,
    ContextBatch,
    SingleCallAnswerRelevancy,
    SingleCallFaithfulness,
    context_batches,
)
from .judge import LocalJudge
from .metrics import ApplicationIntegrity
from .run import error_metric_names, make_case, metric_summary, run


class EvaluationSafetyTests(unittest.TestCase):
    def setUp(self):
        env = patch.dict(os.environ, {"DEEPEVAL_JUDGE_BASE_URL": "http://unit-eval.test/v1"})
        env.start()
        self.addCleanup(env.stop)
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
        with patch("evaluation.deepeval_suite.judge.httpx.Client.post", return_value=response):
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
        with patch(
            "evaluation.deepeval_suite.judge.httpx.Client.post", return_value=response
        ) as call:
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
            patch("evaluation.deepeval_suite.judge.httpx.Client.post") as post,
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
        with patch("evaluation.deepeval_suite.judge.httpx.Client.post", return_value=response):
            with self.assertRaisesRegex(
                RuntimeError, "Judge HTTP 502: output_budget_exceeded"
            ) as error:
                LocalJudge().generate("evaluate")
            self.assertNotIn("private text", str(error.exception))

    def test_provider_limit_does_not_send_more_requests(self):
        response = httpx.Response(
            502,
            request=httpx.Request("POST", "http://local"),
            json={"detail": "Provider usage limit reached"},
        )
        judge = LocalJudge()
        with patch(
            "evaluation.deepeval_suite.judge.httpx.Client.post", return_value=response
        ) as post:
            for _ in range(3):
                with self.assertRaisesRegex(RuntimeError, "provider_usage_limit"):
                    judge.generate("evaluate")
        self.assertEqual(post.call_count, 1)
        self.assertEqual(judge.requests, 1)

    def test_runner_stops_remaining_cases_after_provider_failure(self):
        judge = LocalJudge()

        def evaluate(**kwargs):
            judge.blocked_error = "Judge HTTP 502: provider_usage_limit"
            return SimpleNamespace(
                model_dump=lambda mode: {
                    "test_results": [
                        {
                            "success": False,
                            "metrics_data": [
                                {
                                    "name": kwargs["metrics"][0].__name__,
                                    "error": "provider_usage_limit",
                                    "score": None,
                                    "success": False,
                                }
                            ],
                        }
                    ]
                }
            )

        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            golden = directory / "golden.jsonl"
            predictions = directory / "predictions.jsonl"
            cases = [self.golden, {**self.golden, "id": "NEXT"}]
            golden.write_text("\n".join(json.dumps(c) for c in cases))
            predictions.write_text(
                "\n".join(
                    json.dumps({"id": c["id"], "score": {"outcome": "scored"}}) for c in cases
                )
            )
            args = SimpleNamespace(
                integrity_only=False,
                golden=golden,
                predictions=predictions,
                ids="",
                max_cases=30,
                output=directory / "results",
            )
            with (
                patch("evaluation.deepeval_suite.run.LocalJudge", return_value=judge),
                patch("evaluation.deepeval_suite.run.evaluate", side_effect=evaluate) as call,
            ):
                run(args)
            result = json.loads((args.output / "summary.json").read_text())
        self.assertEqual(call.call_count, 1)
        self.assertEqual(result["cases"], 1)
        self.assertEqual(result["pending_case_ids"], ["NEXT"])
        self.assertIn("usage_limit", result["stopped_reason"])


class BatchedMetricTests(unittest.TestCase):
    def setUp(self):
        env = patch.dict(os.environ, {"DEEPEVAL_JUDGE_BASE_URL": "http://unit-eval.test/v1"})
        env.start()
        self.addCleanup(env.stop)
        self.judge = LocalJudge()
        self.case = LLMTestCase(
            input="What is the retention rule?",
            actual_output="Keep the records. Approval is required. No early destruction.",
            expected_output="REFERENCE_SHOULD_NOT_LEAK_INTO_FAITHFULNESS",
            retrieval_context=["Keep the records. Approval is required."],
        )

    def test_answer_and_faithfulness_use_one_call_and_builtin_scoring(self):
        response = AnswerVerdicts.model_validate(
            {
                "statements": [
                    {"statement": "Keep records", "verdict": "yes"},
                    {"statement": "Approval", "verdict": "no"},
                    {"statement": "No early destruction", "verdict": "borderline"},
                ],
                "reason": "One negative and one borderline statement.",
            }
        )
        for cls in [SingleCallAnswerRelevancy, SingleCallFaithfulness]:
            with self.subTest(metric=cls.__name__):
                metric = cls(model=self.judge, threshold=0.7, async_mode=False)
                with patch.object(self.judge, "generate", return_value=response) as generate:
                    self.assertAlmostEqual(metric.measure(self.case), 2 / 3)
                self.assertEqual(generate.call_count, 1)
                self.assertFalse(metric.success)
                self.assertNotIn(self.case.expected_output, generate.call_args.args[0])

    def test_fourteen_contexts_use_one_call_and_preserve_item_order(self):
        self.case.retrieval_context = [f"Evidence {i}" for i in range(14)]
        response = ContextBatch.model_validate(
            {
                "contexts": [
                    {
                        "context_index": i,
                        "statements": [{"statement": f"Evidence {i}", "verdict": "yes"}],
                    }
                    for i in reversed(range(14))
                ],
                "reason": "All relevant.",
            }
        )
        metric = BatchedContextualRelevancy(model=self.judge, async_mode=False)
        with patch.object(self.judge, "generate", return_value=response) as generate:
            self.assertEqual(metric.measure(self.case), 1)
        self.assertEqual(generate.call_count, 1)
        self.assertEqual(
            [v.verdicts[0].statement for v in metric.verdicts_list], self.case.retrieval_context
        )

    def test_relevance_is_weighted_by_statements_not_contexts(self):
        self.case.retrieval_context = ["One fact", "Three facts"]
        response = ContextBatch.model_validate(
            {
                "contexts": [
                    {"context_index": 0, "statements": [{"statement": "One", "verdict": "yes"}]},
                    {
                        "context_index": 1,
                        "statements": [{"statement": str(i), "verdict": "no"} for i in range(3)],
                    },
                ],
                "reason": "One relevant statement out of four.",
            }
        )
        metric = BatchedContextualRelevancy(model=self.judge, async_mode=False)
        with patch.object(self.judge, "generate", return_value=response):
            self.assertEqual(metric.measure(self.case), 0.25)

    def test_large_context_set_uses_bounded_batches_without_dropping_items(self):
        self.case.retrieval_context = [f"Evidence {i}" for i in range(8)]

        def generate(prompt, schema):
            data = json.loads(prompt.split("DATA:\n", 1)[1])
            return schema.model_validate(
                {
                    "contexts": [
                        {
                            "context_index": c["context_index"],
                            "statements": [{"statement": c["text"], "verdict": "yes"}],
                        }
                        for c in data["contexts"]
                    ],
                    "reason": "Relevant.",
                }
            )

        metric = BatchedContextualRelevancy(model=self.judge, async_mode=False)
        with (
            patch.dict(os.environ, {"DEEPEVAL_CONTEXT_BATCH_MAX_ITEMS": "3"}),
            patch.object(self.judge, "generate", side_effect=generate) as call,
        ):
            self.assertEqual(metric.measure(self.case), 1)
        self.assertEqual(call.call_count, 3)
        self.assertEqual(len(metric.verdicts_list), 8)

    def test_missing_or_duplicate_context_ids_are_errors(self):
        self.case.retrieval_context = ["First", "Second"]
        for indices in [[0], [0, 0], [0, 2]]:
            response = ContextBatch.model_validate(
                {
                    "contexts": [
                        {"context_index": i, "statements": [{"statement": "X", "verdict": "yes"}]}
                        for i in indices
                    ],
                    "reason": "Invalid coverage.",
                }
            )
            metric = BatchedContextualRelevancy(model=self.judge, async_mode=False)
            with patch.object(self.judge, "generate", return_value=response):
                with self.assertRaisesRegex(ValueError, "context IDs"):
                    metric.measure(self.case)
            self.assertIsNone(metric.score)

    def test_oversized_context_is_rejected_without_truncation(self):
        with self.assertRaisesRegex(ValueError, "no truncation"):
            list(context_batches(["Too much text"], max_items=16, max_chars=3))

    def test_full_deepeval_case_uses_six_calls_for_fourteen_contexts(self):
        from .run import load_jsonl

        golden = load_jsonl(Path(__file__).resolve().parents[1] / "financial-v1/golden.jsonl")[0]
        prediction = {
            "id": golden["id"],
            "score": {"outcome": "scored", "checks": {}},
            "response": {
                "result": {
                    "claims": [{"text": "Keep records for eight years."}],
                    "evidence": {f"e{i}": {"text": f"Evidence {i}"} for i in range(14)},
                }
            },
        }

        def generate(prompt, schema=None, **kwargs):
            fields = schema.model_fields
            if "score" in fields:
                return schema.model_validate({"score": 10, "reason": "Correct."})
            if "contexts" in fields:
                data = json.loads(prompt.split("DATA:\n", 1)[1])
                return schema.model_validate(
                    {
                        "contexts": [
                            {
                                "context_index": c["context_index"],
                                "statements": [{"statement": c["text"], "verdict": "yes"}],
                            }
                            for c in data["contexts"]
                        ],
                        "reason": "Relevant.",
                    }
                )
            if "statements" in fields:
                return schema.model_validate(
                    {
                        "statements": [{"statement": "Keep records", "verdict": "yes"}],
                        "reason": "Relevant and supported.",
                    }
                )
            return schema.model_validate(
                {"verdicts": [{"verdict": "yes", "reason": "Relevant."} for _ in range(14)]}
            )

        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            gold = directory / "golden.jsonl"
            pred = directory / "predictions.jsonl"
            gold.write_text(json.dumps(golden) + "\n")
            pred.write_text(json.dumps(prediction) + "\n")
            args = SimpleNamespace(
                integrity_only=False,
                golden=gold,
                predictions=pred,
                ids="",
                max_cases=30,
                output=directory / "results",
                retry_errors=None,
            )
            with (
                patch("evaluation.deepeval_suite.run.LocalJudge", return_value=self.judge),
                patch.object(self.judge, "generate", side_effect=generate) as call,
            ):
                run(args)
                args.resume = True
                run(args)
            summary = json.loads((args.output / "summary.json").read_text())
            row = json.loads((args.output / "results.jsonl").read_text())
        self.assertEqual(call.call_count, 6)
        self.assertEqual(len(row["evaluation"]["metrics_data"]), 7)
        self.assertEqual(summary["judge_error_cases"], 0)
        self.assertEqual(summary["metric_protocol"], "durable_batched_v2")


class DurableCheckpointTests(unittest.TestCase):
    def test_lock_configuration_validation_and_low_score_roundtrip(self):
        from .checkpoints import Checkpoints

        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder)
            store = Checkpoints(output, {"model": "test"}, "mock://durable-test")
            try:
                record = {"metrics_data": [{"name": "test", "score": 0.1, "error": None}]}
                store.put("case", "test", record, 1)
                self.assertEqual(store.get("case", "test")["record"], record)
                with self.assertRaisesRegex(RuntimeError, "already using"):
                    Checkpoints(output, {"model": "test"}, "mock://durable-test")
            finally:
                store.close()
            with self.assertRaisesRegex(ValueError, "configuration/input changed"):
                Checkpoints(output, {"model": "changed"}, "mock://durable-test")


if __name__ == "__main__":
    unittest.main()
