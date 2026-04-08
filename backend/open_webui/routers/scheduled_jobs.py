import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from open_webui.models.scheduled_jobs import ScheduledJobForm, ScheduledJobModel, ScheduledJobs
from open_webui.utils.auth import get_verified_user

log = logging.getLogger(__name__)
router = APIRouter()


@router.get("/", response_model=list[ScheduledJobModel])
async def list_scheduled_jobs(user=Depends(get_verified_user)):
    return ScheduledJobs.get_by_user_id(user.id)


@router.post("/", response_model=ScheduledJobModel)
async def create_scheduled_job(form_data: ScheduledJobForm, user=Depends(get_verified_user)):
    return ScheduledJobs.create(user.id, form_data)


@router.get("/{id}", response_model=ScheduledJobModel)
async def get_scheduled_job(id: str, user=Depends(get_verified_user)):
    job = ScheduledJobs.get_by_id(id)
    if not job or job.user_id != user.id:
        raise HTTPException(status_code=404, detail="Not found")
    return job


@router.put("/{id}", response_model=ScheduledJobModel)
async def update_scheduled_job(
    id: str, form_data: ScheduledJobForm, user=Depends(get_verified_user)
):
    job = ScheduledJobs.get_by_id(id)
    if not job or job.user_id != user.id:
        raise HTTPException(status_code=404, detail="Not found")
    updated = ScheduledJobs.update(id, form_data)
    if not updated:
        raise HTTPException(status_code=500, detail="Update failed")
    return updated


@router.delete("/{id}")
async def delete_scheduled_job(id: str, user=Depends(get_verified_user)):
    job = ScheduledJobs.get_by_id(id)
    if not job or job.user_id != user.id:
        raise HTTPException(status_code=404, detail="Not found")
    ScheduledJobs.delete(id)
    return {"ok": True}


@router.post("/{id}/run")
async def run_scheduled_job_now(request: Request, id: str, user=Depends(get_verified_user)):
    job = ScheduledJobs.get_by_id(id)
    if not job or job.user_id != user.id:
        raise HTTPException(status_code=404, detail="Not found")

    from open_webui.utils.scheduler import run_scheduled_job
    import asyncio

    asyncio.create_task(run_scheduled_job(request.app, job))
    return {"ok": True, "message": "Job triggered"}
