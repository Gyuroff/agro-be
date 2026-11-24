import datetime as dt

from pydantic import BaseModel, Field


class CancellationParserOutput(BaseModel):
    date: dt.date | None = Field(
        default=None, description="ISO дата на отказаните курсове; null, ако не е ясна"
    )
    plates: list[str] = Field(default_factory=list, description="Номерата ТОЧНО както са написани")
    reason: str | None = None
