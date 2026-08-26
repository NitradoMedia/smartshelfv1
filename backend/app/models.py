from datetime import datetime
from enum import Enum
from typing import Optional

from sqlalchemy import DateTime, Float, Integer, String, Text, Boolean
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class IncidentStatus(str, Enum):
    open = "open"
    false_alarm = "false_alarm"
    theft = "theft"
    matched = "matched"  # no discrepancy, kept for audit optionally


class Transaction(Base):
    __tablename__ = "transactions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    external_id: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    article_count: Mapped[int] = mapped_column(Integer)
    total_amount: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    cashier: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    register_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    raw_payload: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    source_file: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    processed: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


class Incident(Base):
    __tablename__ = "incidents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    transaction_id: Mapped[int] = mapped_column(Integer, index=True)
    external_id: Mapped[str] = mapped_column(String(128), index=True)
    receipt_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    receipt_articles: Mapped[int] = mapped_column(Integer)
    ai_articles: Mapped[int] = mapped_column(Integer)
    difference: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(32), default=IncidentStatus.open.value, index=True)
    clip_path: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    thumbnail_path: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    ai_backend: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    ai_details: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    reviewed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


class ProcessedFile(Base):
    __tablename__ = "processed_files"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    path: Mapped[str] = mapped_column(String(512), unique=True)
    checksum: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    processed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
