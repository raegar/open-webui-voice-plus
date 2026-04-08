import time
from typing import Optional
from uuid import uuid4

from open_webui.internal.db import Base, get_db
from pydantic import BaseModel
from sqlalchemy import BigInteger, Boolean, Column, Integer, String, Text
from open_webui.internal.db import engine


class ScheduledJob(Base):
    __tablename__ = "scheduled_job"

    id = Column(String, primary_key=True)
    user_id = Column(String, index=True, nullable=False)
    title = Column(Text, nullable=False)
    chat_id = Column(String, nullable=False)
    model_id = Column(String, nullable=False)
    prompt = Column(Text, nullable=False)
    # "daily", "weekdays", "weekends", or comma-separated day numbers "0,1,2" (Mon=0)
    schedule = Column(String, nullable=False, default="daily")
    hour = Column(Integer, nullable=False)
    minute = Column(Integer, nullable=False, default=0)
    enabled = Column(Boolean, nullable=False, default=True)
    last_run_at = Column(BigInteger, nullable=True)
    created_at = Column(BigInteger, nullable=False)
    updated_at = Column(BigInteger, nullable=False)


class ScheduledJobModel(BaseModel):
    id: str
    user_id: str
    title: str
    chat_id: str
    model_id: str
    prompt: str
    schedule: str
    hour: int
    minute: int
    enabled: bool
    last_run_at: Optional[int]
    created_at: int
    updated_at: int

    model_config = {"from_attributes": True}


class ScheduledJobForm(BaseModel):
    title: str
    chat_id: str
    model_id: str
    prompt: str
    schedule: str = "daily"
    hour: int
    minute: int = 0
    enabled: bool = True


def _is_due(job: ScheduledJobModel, hour: int, minute: int, weekday: int) -> bool:
    """Return True if this job should fire at the given hour/minute/weekday."""
    if job.hour != hour or job.minute != minute:
        return False
    schedule = job.schedule.lower().strip()
    if schedule == "daily":
        return True
    if schedule == "weekdays":
        return weekday < 5
    if schedule == "weekends":
        return weekday >= 5
    # Comma-separated day numbers, e.g. "0,2,4"
    try:
        days = {int(d.strip()) for d in schedule.split(",")}
        return weekday in days
    except ValueError:
        return False


class ScheduledJobsTable:
    def create(self, user_id: str, form_data: ScheduledJobForm) -> ScheduledJobModel:
        with get_db() as db:
            now = int(time.time())
            job = ScheduledJob(
                id=str(uuid4()),
                user_id=user_id,
                title=form_data.title,
                chat_id=form_data.chat_id,
                model_id=form_data.model_id,
                prompt=form_data.prompt,
                schedule=form_data.schedule,
                hour=form_data.hour,
                minute=form_data.minute,
                enabled=form_data.enabled,
                last_run_at=None,
                created_at=now,
                updated_at=now,
            )
            db.add(job)
            db.commit()
            db.refresh(job)
            return ScheduledJobModel.model_validate(job)

    def get_by_user_id(self, user_id: str) -> list[ScheduledJobModel]:
        with get_db() as db:
            jobs = db.query(ScheduledJob).filter_by(user_id=user_id).all()
            return [ScheduledJobModel.model_validate(j) for j in jobs]

    def get_by_id(self, id: str) -> Optional[ScheduledJobModel]:
        with get_db() as db:
            job = db.query(ScheduledJob).filter_by(id=id).first()
            return ScheduledJobModel.model_validate(job) if job else None

    def update(self, id: str, form_data: ScheduledJobForm) -> Optional[ScheduledJobModel]:
        with get_db() as db:
            job = db.query(ScheduledJob).filter_by(id=id).first()
            if not job:
                return None
            for field, value in form_data.model_dump().items():
                setattr(job, field, value)
            job.updated_at = int(time.time())
            db.commit()
            db.refresh(job)
            return ScheduledJobModel.model_validate(job)

    def set_last_run(self, id: str) -> None:
        with get_db() as db:
            job = db.query(ScheduledJob).filter_by(id=id).first()
            if job:
                job.last_run_at = int(time.time())
                job.updated_at = int(time.time())
                db.commit()

    def delete(self, id: str) -> bool:
        with get_db() as db:
            job = db.query(ScheduledJob).filter_by(id=id).first()
            if not job:
                return False
            db.delete(job)
            db.commit()
            return True

    def get_due_jobs(self, hour: int, minute: int, weekday: int) -> list[ScheduledJobModel]:
        with get_db() as db:
            candidates = (
                db.query(ScheduledJob)
                .filter_by(enabled=True, hour=hour, minute=minute)
                .all()
            )
            models = [ScheduledJobModel.model_validate(j) for j in candidates]
            return [j for j in models if _is_due(j, hour, minute, weekday)]


ScheduledJobs = ScheduledJobsTable()

# Create table if it doesn't exist (Alembic manages upstream tables; we create ours directly)
ScheduledJob.__table__.create(bind=engine, checkfirst=True)
