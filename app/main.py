from __future__ import annotations

import asyncio
import io
import uuid
from datetime import datetime, timezone
from typing import Dict, Optional, List, Literal

from fastapi import (
    FastAPI,
    UploadFile,
    File,
    Form,
    HTTPException,
    status,
    Query
)
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from PyPDF2 import PdfReader

app = FastAPI(title="Print Queue", version="1.1.0")

JobStatus = Literal["queued", "printing", "done", "canceled", "error"]


class JobShortOut(BaseModel):
    job_id: str
    filename: str
    pages: int
    status: JobStatus
    created_at: str


class JobFullOut(BaseModel):
    job_id: str
    title: Optional[str] = None
    filename: str
    pages: int
    status: JobStatus
    created_at: str
    updated_at: str


class Job:
    def __init__(
        self,
            job_id: str,
            title: Optional[str],
            filename: str,
            data: bytes,
            pages: int
    ) -> None:
        now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        self.job_id = job_id
        self.title = title
        self.filename = filename
        self.pages = pages
        self.status: JobStatus = "queued"
        self.created_at = now
        self.updated_at = now
        self.data = data
        self._cancel_requested = False

    def to_short_out(self) -> JobShortOut:
        return JobShortOut(
            job_id=self.job_id,
            filename=self.filename,
            pages=self.pages,
            status=self.status,
            created_at=self.created_at,
        )

    def to_full_out(self) -> JobFullOut:
        return JobFullOut(
            job_id=self.job_id,
            title=self.title,
            filename=self.filename,
            pages=self.pages,
            status=self.status,
            created_at=self.created_at,
            updated_at=self.updated_at,
        )


jobs: Dict[str, Job] = {}
queue: asyncio.Queue[str] = asyncio.Queue()
lock = asyncio.Lock()

PRINT_DELAY_PER_PAGE = 0.2  # Збільшити для перевірки скасування


async def printer_loop() -> None:
    while True:
        job_id = await queue.get()
        async with lock:
            job = jobs.get(job_id)
        if not job:
            queue.task_done()
            continue

        if job.status == "canceled":
            queue.task_done()
            continue

        async with lock:
            job.status = "printing"
            job.updated_at = (
                datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
            )

        try:
            for _ in range(job.pages):
                if job._cancel_requested:
                    async with lock:
                        job.status = "canceled"
                        job.updated_at = (
                            datetime.now(timezone.utc)
                            .isoformat()
                            .replace("+00:00", "Z")
                        )
                    break
                await asyncio.sleep(PRINT_DELAY_PER_PAGE)
            else:
                async with lock:
                    job.status = "done"
                    job.updated_at = (
                        datetime.
                        now(timezone.utc).isoformat().
                        replace("+00:00", "Z")
                    )
        except Exception:
            async with lock:
                job.status = "error"
                job.updated_at = (
                    datetime.now(timezone.utc).isoformat().
                    replace("+00:00", "Z")
                )
        finally:
            queue.task_done()


@app.on_event("startup")
async def startup_event() -> None:
    asyncio.create_task(printer_loop())


MAX_FILE_SIZE = 10 * 1024 * 1024


def ensure_pdf(file_upload: UploadFile, data: bytes) -> None:
    if file_upload is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Missing file."
        )
    if file_upload.content_type not in (
        "application/pdf",
        "application/x-pdf",
        "binary/octet-stream",
    ):
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="Content-Type must be application/pdf.",
        )
    if len(data) == 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Empty file."
        )
    if len(data) > MAX_FILE_SIZE:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="File too large (>10MB)."
        )


def get_pages_count(data: bytes) -> int:
    try:
        reader = PdfReader(io.BytesIO(data))
        pages = len(reader.pages)
        if pages <= 0:
            raise ValueError("PDF has zero pages.")
        return pages
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=f"Invalid PDF: {e}"
        )


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


@app.post(
    "/jobs",
    status_code=status.HTTP_201_CREATED,
    response_model=JobShortOut
)
async def create_job(
    file: UploadFile = File(...), title: Optional[str] = Form(default=None)
) -> JobShortOut:
    data = await file.read()
    ensure_pdf(file, data)
    pages = get_pages_count(data)
    job_id = str(uuid.uuid4())

    job = Job(job_id, title, file.filename or "document.pdf", data, pages)

    async with lock:
        jobs[job_id] = job
    await queue.put(job_id)

    return job.to_short_out()


@app.get("/jobs", response_model=List[JobFullOut])
async def list_jobs(
        status: Optional[JobStatus] = Query(default=None)
) -> list[JobFullOut]:
    async with lock:
        values = list(jobs.values())
    if status:
        values = [j for j in values if j.status == status]
    values.sort(key=lambda j: j.created_at)
    return [j.to_full_out() for j in values]


@app.get("/jobs/{job_id}", response_model=JobFullOut)
async def get_job(job_id: str) -> JobFullOut:
    async with lock:
        job = jobs.get(job_id)
    if not job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Job not found."
        )
    return job.to_full_out()


@app.post("/jobs/{job_id}/cancel", response_model=JobFullOut)
async def cancel_job(job_id: str) -> JobFullOut:
    async with lock:
        job = jobs.get(job_id)
        if not job:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Job not found."
            )
        if job.status not in ("queued", "printing"):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Cannot cancel job in status '{job.status}'.",
            )
        if job.status == "queued":
            job.status = "canceled"
            job.updated_at = now_iso()
        else:
            job._cancel_requested = True
    async with lock:
        return jobs[job_id].to_full_out()


@app.get("/jobs/{job_id}/file")
async def download_file(job_id: str) -> StreamingResponse:
    async with lock:
        job = jobs.get(job_id)
    if not job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Job not found."
        )
    return StreamingResponse(
        io.BytesIO(job.data),
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{job.filename}"'
        },
    )
