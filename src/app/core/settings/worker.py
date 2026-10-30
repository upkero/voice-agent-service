from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class WorkerSettings(BaseSettings):
    """How many calls the agent worker keeps ready, how big one may grow, and where it is scraped.

    Each prewarmed process holds its own Silero, turn model and any local STT
    model (a faster_whisper fallback included), about 640 MB with Whisper small.
    livekit-agents defaults to one per CPU up to four, which on a 4-vCPU host is
    2.5 GB spent waiting for calls a portfolio rarely gets at once.
    """

    num_idle_processes: int = Field(
        default=1,
        ge=0,
        le=8,
        description="Prewarmed job processes. A second simultaneous caller waits for one to prewarm.",
    )
    job_memory_limit_mb: float = Field(
        default=1500,
        ge=0,
        description="A call whose process passes this is killed rather than starving the host. 0 disables it.",
    )

    prometheus_port: int = Field(
        default=9464,
        ge=0,
        le=65535,
        description="Port of the worker's /metrics endpoint, summed across job processes. 0 disables it.",
    )

    model_config = SettingsConfigDict(env_prefix="WORKER_", env_file=".env", extra="ignore")


@lru_cache(maxsize=1)
def get_worker_settings() -> WorkerSettings:
    return WorkerSettings()
