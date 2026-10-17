from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

CheckStatus = Literal["ok", "error"]


class LivenessResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["ok"] = "ok"


class DependencyCheck(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: CheckStatus
    latency_ms: float = Field(ge=0, description="Time spent on the check, in milliseconds")


class ReadinessChecks(BaseModel):
    model_config = ConfigDict(extra="forbid")

    database: DependencyCheck
    redis: DependencyCheck


class ReadinessResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["ok", "unavailable"]
    checks: ReadinessChecks
