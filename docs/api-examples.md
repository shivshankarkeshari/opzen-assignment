# API examples

Interactive OpenAPI documentation: <http://127.0.0.1:8000/docs>. The JSON schema is at `/openapi.json`. API keys below are deliberately public local-demo identities, not production authentication.

## Upload and inspect

```bash
curl -sS http://127.0.0.1:8000/v1/documents \
  -H 'X-API-Key: analyst-local-key' \
  -H 'Idempotency-Key: demo-upload-001' \
  -F 'file=@output/pdf/financial-ui-tests/02_finance_controls_2026.pdf;type=application/pdf' \
  -F 'metadata={"title":"Finance Controls Policy 2026","document_type":"policy","department_code":"Finance","approval_status":"approved","effective_from":"2026-01-01"}'
```

The asynchronous response supplies `document_id`, `version_id`, `run_id` and processing status. Inspect progress in the UI or `GET /v1/runs/{run_id}`. Uploading an identical request with the same idempotency key reuses its operation; changing its content returns 409. The upload examples are illustrative, not a transcript from a fresh installation.

```bash
curl -sS http://127.0.0.1:8000/v1/documents \
  -H 'X-API-Key: analyst-local-key'
```

Each document includes `document_id`, `latest_version`, `applicable_version_id` and `revision_count`. Copy returned identifiers into subsequent requests; IDs differ between installations.

## Search

```bash
curl -sS http://127.0.0.1:8000/v1/search \
  -H 'X-API-Key: analyst-local-key' -H 'Content-Type: application/json' \
  -d '{"query":"minimum onboarding retention period","scope":"current","limit":4}'
```

Response fields include `items`, `warnings`, `retrieval_mode` and `resolved_effective_on`. Items contain body text, revision metadata, scores and verified source spans. `scores.semantic` is cosine distance (lower is better); `scores.rrf` is rank fusion, not a confidence probability. Supported scopes are `current`, `effective_on` with an ISO date, and `selected_versions` with explicit version IDs.

## Ask and poll

```bash
curl -sS http://127.0.0.1:8000/v1/answers \
  -H 'X-API-Key: analyst-local-key' \
  -H 'Idempotency-Key: demo-answer-001' -H 'Content-Type: application/json' \
  -d '{"query":"How long must closed onboarding files be retained?","scope":"current","intent":"answer"}'
```

Example response shape (202):

```json
{"run_id":"<returned UUID>","status":"queued"}
```

```bash
curl -sS 'http://127.0.0.1:8000/v1/answers/<returned UUID>' \
  -H 'X-API-Key: analyst-local-key'
```

The response contains status, progress and the question. Once completed, `result.claims[].text` contains verified generated answer text, `claims[].citations` references exact quotes/evidence IDs, and `result.evidence` contains the actual supporting material. `gaps`, `stop_reason`, `iterations`, `freshness_state` and review state explain limitations. A clarification returns `waiting_for_input`; resume through `POST /v1/runs/{run_id}/resume` using its interrupt ID and reply.

Use `intent=compare` with exactly two explicitly selected revisions, `intent=conflicts` for candidate conflicts, or `intent=investigate` for investigations. Follow-ups supply `parent_run_id` and retain the parent's selected scope. `GET /v1/answers?limit=100` lists owned saved questions (maximum 100).

## Permissions, revisions and review

| Endpoint | Purpose |
|---|---|
| `POST /v1/documents/{document_id}/versions` | Upload a content revision with an idempotency key. |
| `POST /v1/versions/{version_id}/reprocess` | New processing revision using current configuration. |
| `GET /v1/versions/{version_id}/outline` | Authorized canonical section map. |
| `GET /v1/chunks/{chunk_id}/evidence` | Canonical subsection and source locators. |
| `POST /v1/answers/{run_id}/review` | Confirm, dismiss or leave a conflict report unresolved, with rationale and optimistic version checking. |

See OpenAPI for exact permission-management, cancellation, retry and review request schemas. Missing authentication returns 401; unavailable sources are withheld without exposing their content. No applicable ready source can return 422 before an answer run is created. An unconfigured LLM returns 503; a model failure after submission is reported in the persisted run outcome, not a fabricated answer. A provider-failed run must not be counted as a correct abstention.
