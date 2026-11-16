"""Verify real Streamlit rendering of compatible-provider runs without regenerating answers."""

import argparse
import json
import time
from datetime import date
from pathlib import Path

import httpx

from backend.config import settings
from scripts.check_financial_ui import checked, open_page, scope, widget
from scripts.financial_test_docs import OUTPUT


def main(predictions, submit=False, output=None):
    rows = [json.loads(line) for line in predictions.read_text().splitlines()]
    receipts = json.loads((OUTPUT / "receipts.json").read_text())
    docs = [r["document_id"] for r in receipts]
    report = []
    report_path = output or OUTPUT / "ui-results-lmstudio.json"
    expected_models = {row["model"] for row in rows if row.get("model")}

    def record(name, passed, **details):
        report.append({"scenario": name, "passed": bool(passed), **details})
        report_path.write_text(json.dumps(report, indent=2))
        print(name, "PASS" if passed else "FAIL", flush=True)

    health = httpx.get("http://127.0.0.1:8000/health", timeout=20).json()
    record(
        "API uses selected generation model",
        health.get("llm_provider") == "openai_compatible"
        and expected_models == {health.get("llm_model")},
    )
    library = open_page("Documents")
    record(
        "Five ready PDFs in analyst library",
        library.metric[0].value == "5" and library.metric[1].value == "5",
    )
    reviewer = open_page("Documents", "reviewer")
    record("Reviewer sees four documents", reviewer.metric[0].value == "4")
    search = open_page("Search")
    scope(search, [docs[1]])
    widget(search, "text_area", "Question").set_value("urgent settlement final approval conditions")
    checked(widget(search, "button", "Search").click().run())
    checked(next(b for b in search.button if b.label == "Read subsection").click().run())
    text = " ".join(item.value for item in search.get("text"))
    record("Cross-page subsection expansion", "Director of Finance" in text and "2,000,000" in text)
    source = next(b for b in search.button if b.label == "Open exact revision")
    checked(source.click().run())
    record("Exact source revision opens", bool(search.query_params.get("version")))
    dated = open_page("Search")
    checked(widget(dated, "selectbox", "Evidence scope").set_value("Effective on date").run())
    widget(dated, "date_input", "Effective date").set_value(date(2025, 6, 30))
    widget(dated, "text_area", "Question").set_value("retention")
    checked(widget(dated, "button", "Search").click().run())
    titles = [s.value for s in dated.subheader]
    record("Historical evidence scope", bool(titles) and all("2025" in t for t in titles))
    apps = {}
    goldens = {
        c["id"]: c
        for c in map(
            json.loads, Path("evaluation/financial-v1/golden.jsonl").read_text().splitlines()
        )
    }
    for row in rows:
        response = row.get("response") or {}
        run_id = response.get("run_id")
        if not run_id:
            continue  # API rejection/dependency block has no saved answer view.
        actor = goldens[row["id"]]["actor"]
        if actor not in apps:
            apps[actor] = open_page("Ask & investigate", actor)
        app = apps[actor]
        checked(widget(app, "selectbox", "Saved questions").set_value(run_id).run())
        headings = [s.value for s in app.subheader]
        record(
            f"{row['id']} saved answer renders",
            response["question"] in headings,
            run_id=run_id,
            answer_quality=row["score"]["automatic_checks_passed"],
        )
        result = response.get("result") or {}
        if not result.get("claims") and response["status"] == "completed":
            record(
                f"{row['id']} unsupported answer is visible",
                any("not enough supported evidence" in i.value for i in app.info),
            )
        if any(c.get("citations") for c in result.get("claims", [])):
            record(
                f"{row['id']} citation source controls render",
                any(b.label == "Open exact revision" for b in app.button),
            )
        if response["status"] == "waiting_for_input":
            record(
                f"{row['id']} clarification control renders",
                any(t.label == "Clarification" for t in app.text_area),
            )
    if submit:
        app = open_page("Ask & investigate")
        scope(app, [docs[0]])
        checked(widget(app, "selectbox", "Evidence scope").set_value("Effective on date").run())
        widget(app, "date_input", "Effective date").set_value(date(2025, 6, 30))
        widget(app, "text_area", "Question").set_value(
            "On 2025-06-30, what minimum retention period applied under Finance Controls Policy 1.0?"
        )
        checked(widget(app, "button", "Start").click().run())
        run_id = app.session_state["agent_run"]
        with httpx.Client(
            base_url="http://127.0.0.1:8000", timeout=30, headers={"X-API-Key": "analyst-local-key"}
        ) as client:
            for _ in range((settings().agent_timeout_seconds + 60) // 2):
                response = client.get(f"/v1/answers/{run_id}")
                response.raise_for_status()
                body = response.json()
                if body["status"] not in ("queued", "running"):
                    break
                time.sleep(2)
        checked(app.run())
        answer_path = (
            report_path.with_name(report_path.stem + "-answer.json")
            if output
            else OUTPUT / "lmstudio-ui-answer.json"
        )
        answer_path.write_text(json.dumps(body, indent=2))
        result = body.get("result") or {}
        record(
            "UI Start reaches terminal worker result",
            body["status"] == "completed" and result.get("iterations", 99) <= 5,
            run_id=run_id,
            stop_reason=result.get("stop_reason"),
        )
        claims = result.get("claims", [])
        text = " ".join(c["text"] for c in claims).lower()
        record(
            "UI-submitted historical answer contains cited retention fact",
            any(t in text for t in ("5 years", "five years"))
            and bool(claims)
            and all(c.get("citations") for c in claims),
            run_id=run_id,
        )
    print(f"UI rendering checks: {sum(r['passed'] for r in report)}/{len(report)}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--predictions", type=Path, required=True)
    parser.add_argument(
        "--output", type=Path, help="Save this UI report separately from earlier models"
    )
    parser.add_argument(
        "--submit", action="store_true", help="Also submit one real question through UI Start"
    )
    args = parser.parse_args()
    main(args.predictions, args.submit, args.output)
