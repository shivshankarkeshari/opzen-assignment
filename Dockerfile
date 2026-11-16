FROM python:3.12.12-slim-bookworm

RUN apt-get update && apt-get install -y --no-install-recommends libglib2.0-0 libgomp1 \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir uv==0.13.5

WORKDIR /app
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    LANGGRAPH_STRICT_MSGPACK=true \
    OMP_NUM_THREADS=2 \
    TOKENIZERS_PARALLELISM=false

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY backend ./backend
COPY ui ./ui
COPY scripts ./scripts
COPY .streamlit ./.streamlit

CMD ["python", "-m", "uvicorn", "backend.api:app", "--host", "0.0.0.0", "--port", "8000"]
