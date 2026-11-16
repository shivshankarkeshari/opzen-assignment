"""Portable golden-set contracts, independent of provider and application UUIDs."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Evidence(BaseModel):
    model_config = ConfigDict(extra="forbid")
    document: str
    page: int = Field(ge=1, le=3)
    quote: str = Field(min_length=1)


class Fact(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    statement: str
    answer_terms_any: list[str] = Field(min_length=1)
    evidence: list[Evidence] = Field(min_length=1)


class Scope(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: Literal["current", "effective_on", "selected_versions"]
    documents: list[str] = Field(min_length=1, max_length=5)
    effective_on: str | None

    @model_validator(mode="after")
    def dates(self):
        if (self.mode == "effective_on") != bool(self.effective_on):
            raise ValueError("Effective date must match the date scope")
        return self


class Expected(BaseModel):
    model_config = ConfigDict(extra="forbid")
    answerability: Literal["answerable", "partial", "unanswerable", "clarification"]
    reference_answer: str
    required_facts: list[Fact]
    minimum_distinct_cited_documents: int = Field(ge=0, le=5)
    forbidden_answer_strings: list[str]


class Checks(BaseModel):
    model_config = ConfigDict(extra="forbid")
    max_agent_iterations: int = Field(ge=1, le=5)
    exact_citation_quotes: bool
    current_access_required: bool
    provider_failures_are_blocked_not_passed: bool


class GoldenCase(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(pattern=r"^FIN-\d{3}$")
    category: str
    question: str
    actor: Literal["analyst", "reviewer"]
    scope: Scope
    expected: Expected
    checks: Checks
    review_notes: str
    parent_case_id: str | None = None
    clarification_reply: str | None = None
