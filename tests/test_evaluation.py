from scripts.evaluate_golden import score


def negative_case():
    return {
        "expected": {
            "answerability": "unanswerable",
            "required_facts": [],
            "forbidden_answer_strings": ["restricted marker"],
            "minimum_distinct_cited_documents": 0,
        },
        "scope": {"documents": ["policy.pdf"]},
        "checks": {"max_agent_iterations": 5},
    }


def test_provider_failure_is_not_a_successful_abstention():
    response = {
        "status": "completed",
        "result": {
            "claims": [],
            "gaps": ["Insufficient supported evidence"],
            "stop_reason": "model_rate_limited",
        },
    }
    result = score(negative_case(), response, {})
    assert result["outcome"] == "provider_blocked" and not result["automatic_checks_passed"]


def test_deadline_is_not_a_successful_negative_answer():
    response = {
        "status": "completed",
        "result": {"claims": [], "gaps": ["deadline"], "stop_reason": "deadline"},
    }
    assert score(negative_case(), response, {})["outcome"] == "budget_blocked"


def test_invalid_request_is_not_confused_with_missing_eligible_evidence():
    assert (
        score(negative_case(), {"detail": "Invalid date field"}, {}, 422)["outcome"] == "api_error"
    )
    assert (
        score(negative_case(), {"detail": "No ready applicable revisions in this scope"}, {}, 422)[
            "outcome"
        ]
        == "no_eligible_source"
    )


def test_forbidden_output_is_checked_even_on_negative_cases():
    response = {
        "status": "completed",
        "result": {
            "claims": [],
            "gaps": ["Not provided; restricted marker"],
            "stop_reason": "insufficient",
            "iterations": 2,
        },
    }
    result = score(negative_case(), response, {})
    assert not result["automatic_checks_passed"]
    assert result["forbidden_strings_found"] == ["restricted marker"]


def duration_answer(text, gaps=None, quote="Records must be retained for seven years."):
    case = negative_case()
    case["expected"].update(
        answerability="answerable",
        minimum_distinct_cited_documents=1,
        required_facts=[{"id": "retention", "answer_terms_any": ["seven years"], "evidence": []}],
    )
    response = {
        "status": "completed",
        "result": {
            "claims": [{"text": text, "citations": [{"evidence_id": "e1", "quote": quote}]}],
            "gaps": gaps or [],
            "iterations": 2,
            "stop_reason": "sufficient",
            "evidence": {
                "e1": {
                    "document_id": "d1",
                    "text": "Records must be retained for seven years.",
                    "sources": [{"page_number": 1}],
                }
            },
        },
    }
    return score(case, response, {"d1": "policy.pdf"})


def test_duration_adjective_does_not_create_a_false_failure():
    assert duration_answer("The seven-year minimum applies to onboarding files.")[
        "automatic_checks_passed"
    ]


def test_required_answer_fact_is_not_credited_only_from_gaps():
    result = duration_answer("A retention minimum applies.", gaps=["seven years"])
    assert result["fact_term_checks"]["retention"] is False


def test_term_normalization_does_not_relax_exact_citations():
    result = duration_answer(
        "The seven-year minimum applies.", quote="The seven-year minimum applies."
    )
    assert result["fact_term_checks"]["retention"] is True
    assert result["checks"]["exact_quotes"] is False
