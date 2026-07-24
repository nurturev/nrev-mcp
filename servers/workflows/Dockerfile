FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    PATH="/app/.venv/bin:$PATH"

COPY --from=ghcr.io/astral-sh/uv:0.11.7 /uv /uvx /bin/

WORKDIR /app

COPY pyproject.toml uv.lock ./
COPY src ./src
RUN uv sync --frozen --no-dev

# FASTMCP_HOST/FASTMCP_PORT are the mcp SDK's own Settings env-prefix
# convention (FastMCP.Settings, env_prefix="FASTMCP_") — no CLI flags needed.
ENV FASTMCP_HOST=0.0.0.0 \
    FASTMCP_PORT=8080

EXPOSE 8080

CMD ["nrev-workflows-mcp-http"]
