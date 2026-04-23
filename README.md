# ChatDBA

ChatDBA phase 1 provides DingTalk-based SQL optimization using a controlled LangGraph workflow and Tongyi Qianwen generation.

## Local Checks

```bash
pip install -e ".[dev]"
pytest -q
```

## Phase 1 Local Runbook

1. Start dependencies:

```bash
docker compose up -d
```

2. Install Python dependencies:

```bash
pip install -e ".[dev]"
```

3. Run tests:

```bash
./scripts/run-local-checks.sh
```

If your dependencies are installed in a specific virtual environment, point the
script at that interpreter:

```bash
PYTHON_BIN=/path/to/venv/bin/python ./scripts/run-local-checks.sh
```

4. Start API:

```bash
uvicorn chatdba.app.main:app --reload
```
