from pydantic import BaseModel, Field


class SubstituteExplanation(BaseModel):
    unplanned_line_id: int
    explanation: str = Field(min_length=1, description="1–2 изречения на български: защо е сменен камион")


class ReportMatcherOutput(BaseModel):
    explanations: list[SubstituteExplanation]
