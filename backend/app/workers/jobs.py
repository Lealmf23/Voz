from __future__ import annotations

import threading
import time
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Callable

import logging

log = logging.getLogger("leal.jobs")


class Cancelled(Exception):
    pass


@dataclass
class Job:
    id: str
    kind: str
    status: str = "pending"  # pending|preparing|processing|completed|cancelled|error
    stage: str = ""
    progress: float = 0.0
    done: int = 0
    total: int = 0
    error: str | None = None
    result: dict | None = None
    started: float = field(default_factory=time.time)
    finished: float | None = None
    _cancel: threading.Event = field(default_factory=threading.Event, repr=False)

    def check(self) -> None:
        if self._cancel.is_set():
            raise Cancelled()

    def step(self, stage: str, done: int, total: int) -> None:
        self.check()
        self.stage, self.done, self.total = stage, done, total
        self.progress = done / total if total else 0.0

    def to_dict(self) -> dict:
        end = self.finished or time.time()
        elapsed = end - self.started
        eta = elapsed * (1 - self.progress) / self.progress if 0 < self.progress < 1 else None
        return {"id": self.id, "kind": self.kind, "status": self.status, "stage": self.stage,
                "progress": self.progress, "done": self.done, "total": self.total, "error": self.error,
                "result": self.result, "elapsed": elapsed, "eta": eta}


class JobQueue:
    """Una sola tarea de IA a la vez (límite de VRAM); el resto espera en cola."""

    def __init__(self) -> None:
        self._pool = ThreadPoolExecutor(max_workers=1)
        self.jobs: dict[str, Job] = {}

    def submit(self, kind: str, fn: Callable[[Job], dict | None]) -> Job:
        job = Job(id=uuid.uuid4().hex[:12], kind=kind)
        self.jobs[job.id] = job

        def run() -> None:
            if job._cancel.is_set():
                job.status, job.finished = "cancelled", time.time()
                return
            job.status = "processing"
            try:
                job.result = fn(job)
                job.status = "completed"
                job.progress = 1.0
                job.done = job.total
            except Cancelled:
                job.status = "cancelled"
            except MemoryError:
                job.status, job.error = "error", "Memoria insuficiente"
            except Exception as e:  # noqa: BLE001 - se reporta al usuario
                log.error("Job %s falló\n%s", job.id, traceback.format_exc())
                job.status, job.error = "error", f"{type(e).__name__}: {e}"
            finally:
                job.finished = time.time()

        self._pool.submit(run)
        return job

    def cancel(self, job_id: str) -> bool:
        job = self.jobs.get(job_id)
        if not job or job.status in ("completed", "error", "cancelled"):
            return False
        job._cancel.set()
        return True
