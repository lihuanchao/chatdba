from uuid import uuid4

from fastapi import FastAPI
from pydantic import BaseModel

from chatdba.domain.models import TaskStatus


class CreateOptimizationTaskRequest(BaseModel):
    raw_sql: str


class CreateOptimizationTaskResponse(BaseModel):
    task_id: str
    status: TaskStatus


def create_app() -> FastAPI:
    app = FastAPI(title="ChatDBA", version="0.1.0")

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok", "service": "chatdba"}

    @app.post(
        "/internal/tasks/sql-optimization",
        status_code=202,
        response_model=CreateOptimizationTaskResponse,
    )
    def create_sql_optimization_task(
        request: CreateOptimizationTaskRequest,
    ) -> CreateOptimizationTaskResponse:
        _ = request
        return CreateOptimizationTaskResponse(
            task_id=str(uuid4()),
            status=TaskStatus.RECEIVED,
        )

    return app


app = create_app()
