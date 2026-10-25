from __future__ import annotations

from pydantic import BaseModel, Field


class TicketCreateRequest(BaseModel):
    category: str = Field(default="OTHER", max_length=32)
    priority: str = Field(default="NORMAL", max_length=16)
    subject: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1, max_length=5000)


class TicketResponse(BaseModel):
    ticket_id: str
    ticket_no: str
    status: str
    category: str
    priority: str
    subject: str
    created_at: str
