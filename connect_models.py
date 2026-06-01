"""Lightweight connect API models (no scanner imports)."""

from pydantic import BaseModel, Field


class ConnectRequest(BaseModel):
    source_type: str
    connection_config: dict = Field(default_factory=dict)


class ConnectSummary(BaseModel):
    status: str = "success"
    source_type: str
    message: str
