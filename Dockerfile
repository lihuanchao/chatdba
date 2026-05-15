FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        curl \
        gcc \
        default-libmysqlclient-dev \
        postgresql-client \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./
COPY src ./src
COPY migrations ./migrations
COPY scripts ./scripts

RUN pip install --no-cache-dir -e .

EXPOSE 8000

CMD ["uvicorn", "chatdba.app.main:app", "--host", "0.0.0.0", "--port", "8000"]
