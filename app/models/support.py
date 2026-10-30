import uuid
from datetime import datetime
from sqlalchemy import Column, String, Boolean, DateTime, Text
from app.database import Base

def _uuid():
    return str(uuid.uuid4())

def _now():
    return datetime.utcnow()

class SupportMessage(Base):
    __tablename__ = "support_messages"

    id = Column(String, primary_key=True, default=_uuid)
    session_id = Column(String, index=True, nullable=True)
    creator_id = Column(String, index=True, nullable=True)
    creator_handle = Column(String, index=True, nullable=True)
    creator_name = Column(String, nullable=True)
    sender = Column(String, default="user")  # user | assistant | admin | system
    message = Column(Text, nullable=False)
    creator_context = Column(Text, nullable=True)  # JSON serialized creator snapshot
    status = Column(String, default="active")  # active | ai_resolved | escalated_to_admin | pending_admin
    escalated = Column(Boolean, default=False)
    created_at = Column(DateTime, default=_now, index=True)
    updated_at = Column(DateTime, default=_now, onupdate=_now)

