FROM python:3.12-slim
WORKDIR /app
RUN pip install --no-cache-dir uv
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY agentgate ./agentgate
COPY profiles ./profiles
COPY alembic.ini ./
COPY migrations ./migrations
RUN uv sync --frozen --no-dev
ENV AGENTGATE_BIND=0.0.0.0:8400 AGENTGATE_PROFILES_DIR=/app/profiles AGENTGATE_LOG_PATH=/data/decisions.jsonl
ARG GIT_SHA=
ENV AGENTGATE_GIT_SHA=$GIT_SHA
EXPOSE 8400
CMD ["sh", "-c", "uv run alembic upgrade head && uv run python -m agentgate"]
