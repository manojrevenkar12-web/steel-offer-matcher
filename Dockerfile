FROM python:3.13-slim AS app
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYDANTIC_AI_NO_BANNER=1
COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir .
COPY alembic.ini ./
COPY migrations ./migrations
COPY config ./config
COPY knowledge ./knowledge
COPY prompts ./prompts
COPY reports ./reports
COPY evals ./evals
RUN useradd --uid 10001 app && chown -R app /app
USER app
EXPOSE 8000
CMD ["sh", "-c", "alembic upgrade head && uvicorn steelmatch.service.app:app --host 0.0.0.0 --port 8000 --proxy-headers"]
