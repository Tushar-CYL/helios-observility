# HELIOS RCA Copilot service image.
FROM python:3.12-slim

WORKDIR /app

# Install dependencies first for better layer caching.
COPY pyproject.toml ./
COPY helios_rca ./helios_rca
RUN pip install --no-cache-dir .

EXPOSE 8088

# Defaults target the compose network; override via environment as needed.
ENV HELIOS_CLICKHOUSE_URL=http://clickhouse:8123 \
    HELIOS_RCA_REASONER=auto \
    HELIOS_OLLAMA_ENDPOINT=http://host.docker.internal:11434 \
    HELIOS_OLLAMA_MODEL=phi3.5

CMD ["uvicorn", "helios_rca.app:app", "--host", "0.0.0.0", "--port", "8088"]
