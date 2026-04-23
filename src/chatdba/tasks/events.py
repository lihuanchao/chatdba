from datetime import datetime, timezone

from pydantic import BaseModel, Field

from chatdba.domain.models import TaskStatus


class ProgressEvent(BaseModel):
    task_id: str
    status: TaskStatus
    message: str
    payload: dict[str, object] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
