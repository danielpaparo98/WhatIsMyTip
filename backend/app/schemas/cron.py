"""Schemas for cron job management."""

from datetime import datetime
from typing import Optional, Dict, Any, List
from pydantic import BaseModel, Field


class JobExecutionCreate(BaseModel):
    """Schema for creating a job execution record."""
    job_name: str = Field(..., max_length=100)
    status: str = Field(default="pending", max_length=20)


class JobExecutionResponse(BaseModel):
    """Schema for job execution response."""
    id: int
    job_name: str
    status: str
    started_at: datetime
    completed_at: Optional[datetime] = None
    duration_seconds: Optional[int] = None
    items_processed: Optional[int] = None
    items_failed: Optional[int] = None
    error_message: Optional[str] = None
    result_summary: Optional[str] = None
    created_at: datetime
    
    class Config:
        from_attributes = True


class JobStatusResponse(BaseModel):
    """Schema for job status response."""
    job_name: str
    status: str
    last_run: Optional[datetime] = None
    last_duration_seconds: Optional[float] = None
    last_success_at: Optional[datetime] = None
    last_failure_at: Optional[datetime] = None
    total_runs: int = 0
    successful_runs: int = 0
    failed_runs: int = 0
    success_rate: float = 0.0
    is_locked: bool = False
    locked_at: Optional[datetime] = None
    locked_by: Optional[str] = None
    expires_at: Optional[datetime] = None


class JobTriggerRequest(BaseModel):
    """Schema for manual job trigger request."""
    force: bool = Field(default=False, description="Force execution even if job is locked")


class JobTriggerResponse(BaseModel):
    """Schema for job trigger response."""
    job_name: str
    status: str
    execution_id: Optional[int] = None
    message: str


class JobLockCreate(BaseModel):
    """Schema for creating a job lock."""
    job_name: str = Field(..., max_length=100)
    locked_by: str = Field(..., max_length=100)
    expires_at: datetime


class JobLockResponse(BaseModel):
    """Schema for job lock response."""
    id: int
    job_name: str
    locked_at: datetime
    locked_by: Optional[str] = None
    expires_at: datetime
    
    class Config:
        from_attributes = True


class JobListResponse(BaseModel):
    """Schema for list of jobs."""
    jobs: List[JobStatusResponse]
    total: int


class JobExecutionListResponse(BaseModel):
    """Schema for list of job executions."""
    executions: List[JobExecutionResponse]
    total: int
    job_name: Optional[str] = None


class JobMetrics(BaseModel):
    """Schema for job metrics."""
    job_name: str
    total_runs: int
    successful_runs: int
    failed_runs: int
    average_duration_seconds: float
    last_run_at: Optional[datetime] = None
    last_success_at: Optional[datetime] = None
    last_failure_at: Optional[datetime] = None
    success_rate: float


class CronHealthResponse(BaseModel):
    """Schema for cron health check."""
    status: str
    timestamp: datetime
    jobs: List[JobStatusResponse]
    database: str
    cron_enabled: bool
