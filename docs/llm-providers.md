> Provider reference. Historical model run reports are archived locally; the submission result report preserves the completed Codex first pass.

# Configurable LLM providers

Checked 15–16 November 2026 against the installed CLI, live local server and official documentation. Embeddings remain the pinned local BGE encoder at 768 dimensions; changing the answer model does not require changing that embedding space.

## Runtime .env settings

The app supports `groq` and `openai_compatible`. `LLM_MODEL` is the actual server model ID, and overrides the provider default. `LLM_MAX_OUTPUT_TOKENS` is provider-independent; legacy Groq settings remain fallback values. Runs pin their model/provider; create a new run when switching providers rather than replaying an old checkpoint against a different model.

The preserved development test used **Codex CLI / `gpt-6.1-sol` via the host gateway** for the generator and judge; a fresh submission setup uses `.env.example` and its LM Studio defaults. `.env.example` retains the LM Studio configuration and documents the optional Codex gateway settings. Qwen uses `LLM_REASONING_EFFORT=none`, `LLM_TIMEOUT_SECONDS=300` and a 1,800-second total agent budget; the exploration ceiling remains five. The live golden-set and Streamlit findings are saved in [the LM Studio evaluation report](evaluation-results.md); connectivity and valid JSON alone do not establish answer quality.

Groq:

```dotenv
LLM_PROVIDER=groq
LLM_MODEL=openai/gpt-oss-120b
LLM_MAX_OUTPUT_TOKENS=2048
GROQ_API_KEY=your-private-key
AGENT_MAX_ITERATIONS=5
```

LM Studio from Docker:

```dotenv
LLM_PROVIDER=openai_compatible
LLM_MODEL=qwen/qwen3.5-9b
LLM_REASONING_EFFORT=none
LLM_TIMEOUT_SECONDS=300
AGENT_TIMEOUT_SECONDS=1800
LLM_MAX_OUTPUT_TOKENS=2048
OPENAI_API_KEY=lm-studio
OPENAI_BASE_URL=http://host.docker.internal:1234/v1
AGENT_MAX_ITERATIONS=5
```

For native Python processes, use `http://127.0.0.1:1234/v1` instead. Recreate the API and worker after editing `.env`:

```bash
docker compose up -d --no-build api worker
```

The local `/v1/models` endpoint advertised `lfm3-omni-2b`, `lfm3-vl-2b`, `qwen/qwen3.5-9b`, `google/gemma-4-e4b`, and `text-embedding-nomic-embed-text-v1.5`. The requested direct model ID is **`lfm3-omni-2b`**, not `openai/lfm3-omni-2b`; the latter can be a routing-library namespace but was not an advertised LM Studio API ID here. The server was reachable through `host.docker.internal` from the API container. A real `lfm3-omni-2b` strict JSON-schema smoke request returned `{"status":"OK"}`. This validates connectivity/schema support for that request, not multi-hop reasoning quality.

The compatible adapter calls `/chat/completions`, validates returned JSON against the same typed planner/answer schemas, omits Groq-only parameters, ignores reasoning fields, and keeps access checks/citation verification/iteration bounds. Model-specific server capabilities still matter; a 2B model should be evaluated on the golden set rather than assumed equivalent to a larger reasoning model. [LM Studio structured outputs](https://lmstudio.ai/docs/developer/openai-compat/structured-output), [model listing](https://lmstudio.ai/docs/developer/openai-compat/models).

## Codex CLI as an alternative

The installed CLI is `0.169.0-alpha.11.1`. Its help supports `codex exec`, `--model`, `--output-schema`, `--ephemeral`, `--sandbox`, and `--output-last-message`. The local model cache includes `gpt-6.1-sol`. This does not prove that a particular account request will succeed; the current live probe has verified `gpt-6.1-sol`; the prefixed `openai/gpt-6.1-sol` was rejected for the ChatGPT-authenticated CLI.

Codex can be used for local automation with schema-constrained final output. The non-interactive form is `codex exec`, not the interactive `codex -m ...` command. The standard documented model slug is `gpt-6.1-sol`:

```bash
codex exec -m gpt-6.1-sol \
  --sandbox read-only --ephemeral --skip-git-repo-check \
  --output-schema response.schema.json \
  --output-last-message response.json -
```

This example shows the CLI invocation; the implemented adapter is [scripts/codex_gateway.py](../scripts/codex_gateway.py). It supplies the schema and authorized context, uses an isolated temporary directory, disables tools and approvals, rejects tool-operation events, and enforces a subprocess deadline and concurrency limit. The outer LangGraph iteration counter is separate from that subprocess deadline. Existing CLI authentication stays on the host. [Non-interactive execution](https://learn.chatgpt.com/docs/non-interactive-mode).

`CODEX_FORCE_AUTH_JSON=1` was not established as a supported public setting by the checked official documentation. The documented credential-storage setting is `cli_auth_credentials_store = "file"` (alternatives: `keyring`, `auto`, `ephemeral`). Saved CLI credentials are used by `codex exec`; they are not an OpenAI API key for the app. `--ae` is not present in the installed `codex exec --help`; use process environment/configuration appropriate to the actual tool instead. [Authentication](https://learn.chatgpt.com/docs/auth).

The direct LM Studio HTTP adapter remains available. A host-side Codex gateway is now implemented and has passed live structured protocol checks; see [the Codex test report and setup](evaluation-results.md). It uses the same HTTP adapter for both app generation and evaluation. For a service deployment using OpenAI directly, use supported API credentials and an API adapter; do not repurpose saved ChatGPT login tokens. The official model ID is `gpt-6.1-sol`, with structured outputs supported, and reasoning-tool workflows use Responses. [Model reference](https://developers.openai.com/api/docs/models/gpt-6.1-sol).
