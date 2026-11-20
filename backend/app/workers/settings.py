from typing import ClassVar

from arq.connections import RedisSettings
from arq.cron import CronJob

from app.core.config import Settings, get_settings
from app.workers import cron, jobs


def _run_timeout_s(settings: Settings) -> int:
    # the runner's wall budget fires first; arq's timeout is only the backstop
    longest = max(budget.wall_s for budget in settings.pipeline.budgets.values())
    return int(longest) + settings.workers.run_timeout_margin_s


class _BaseWorker:
    # arq reads only a settings class's own __dict__, so @worker_settings copies these onto each worker
    settings = get_settings()
    redis_settings = RedisSettings.from_dsn(settings.redis.url.get_secret_value())
    on_startup = jobs.on_startup
    on_shutdown = jobs.on_shutdown
    on_job_start = jobs.on_job_start
    after_job_end = jobs.after_job_end
    allow_abort_jobs = True
    max_tries = settings.workers.max_tries
    keep_result = settings.workers.keep_result_s
    job_completion_wait = settings.workers.job_completion_wait_s
    health_check_interval = settings.workers.health_check_interval_s
    cron_jobs: ClassVar[list[CronJob]] = []


SHARED_OPTIONS = (
    "redis_settings",
    "on_startup",
    "on_shutdown",
    "on_job_start",
    "after_job_end",
    "allow_abort_jobs",
    "max_tries",
    "keep_result",
    "job_completion_wait",
    "health_check_interval",
    "cron_jobs",
)


def worker_settings[T: type](cls: T) -> T:
    for name in SHARED_OPTIONS:
        if name not in cls.__dict__:
            setattr(cls, name, getattr(_BaseWorker, name))
    return cls


@worker_settings
class InteractiveWorkerSettings(_BaseWorker):
    queue_name = _BaseWorker.settings.workers.interactive_queue
    functions: ClassVar[list] = [jobs.run_pipeline]
    max_jobs = _BaseWorker.settings.workers.interactive_max_jobs
    job_timeout = _run_timeout_s(_BaseWorker.settings)


@worker_settings
class BatchWorkerSettings(_BaseWorker):
    queue_name = _BaseWorker.settings.workers.batch_queue
    functions: ClassVar[list] = [jobs.run_pipeline]
    max_jobs = _BaseWorker.settings.workers.batch_max_jobs
    job_timeout = _run_timeout_s(_BaseWorker.settings)


@worker_settings
class MaintenanceWorkerSettings(_BaseWorker):
    # hosts every cron; run two replicas, arq's unique cron slots run each tick once
    queue_name = _BaseWorker.settings.workers.maintenance_queue
    functions: ClassVar[list] = [jobs.purge_client]
    cron_jobs: ClassVar[list[CronJob]] = cron.cron_jobs()
    max_jobs = _BaseWorker.settings.workers.maintenance_max_jobs
    job_timeout = _BaseWorker.settings.workers.job_timeout_s
