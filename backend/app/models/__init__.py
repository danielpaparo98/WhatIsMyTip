from sqlalchemy import Column, Integer, String, Float, DateTime, Text, Boolean, UniqueConstraint
from sqlalchemy.sql import func
from datetime import datetime, timezone
from app.db import Base


class Game(Base):
    __tablename__ = "games"
    
    id = Column(Integer, primary_key=True, index=True)
    squiggle_id = Column(Integer, unique=True, index=True)
    round_id = Column(Integer, index=True)
    season = Column(Integer, index=True)
    home_team = Column(String(100))
    away_team = Column(String(100))
    home_score = Column(Integer, nullable=True)
    away_score = Column(Integer, nullable=True)
    venue = Column(String(200))
    date = Column(DateTime)
    completed = Column(Boolean, default=False)
    predictions_generated = Column(Boolean, default=False, index=True)
    tips_generated = Column(Boolean, default=False, index=True)
    last_synced_at = Column(DateTime(timezone=True), nullable=True)
    sync_version = Column(Integer, default=0)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())


class Tip(Base):
    __tablename__ = "tips"
    
    id = Column(Integer, primary_key=True, index=True)
    game_id = Column(Integer, index=True)
    heuristic = Column(String(50), index=True)  # best_bet, yolo, high_risk_high_reward
    selected_team = Column(String(100))
    margin = Column(Integer)
    confidence = Column(Float)
    explanation = Column(Text)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    
    __table_args__ = (
        UniqueConstraint('game_id', 'heuristic', name='uq_game_heuristic'),
    )


class ModelPrediction(Base):
    __tablename__ = "model_predictions"
    
    id = Column(Integer, primary_key=True, index=True)
    game_id = Column(Integer, index=True)
    model_name = Column(String(50), index=True)  # elo, form, home_advantage, value
    winner = Column(String(100))
    confidence = Column(Float)
    margin = Column(Integer)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    
    __table_args__ = (
        UniqueConstraint('game_id', 'model_name', name='uq_game_model'),
    )


class BacktestResult(Base):
    __tablename__ = "backtest_results"
    
    id = Column(Integer, primary_key=True, index=True)
    heuristic = Column(String(50), index=True)
    season = Column(Integer)
    round_id = Column(Integer)
    tips_made = Column(Integer)
    tips_correct = Column(Integer)
    accuracy = Column(Float)
    profit = Column(Float)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    
    __table_args__ = (
        UniqueConstraint('season', 'round_id', 'heuristic', name='uq_backtest_season_round_heuristic'),
    )


class GenerationProgress(Base):
    __tablename__ = "generation_progress"
    
    id = Column(Integer, primary_key=True, index=True)
    operation_type = Column(String(50), index=True)  # e.g., "historical_generation", "season_sync"
    season = Column(Integer, nullable=True, index=True)
    total_items = Column(Integer, default=0)
    completed_items = Column(Integer, default=0)
    status = Column(String(20), default="pending")  # pending, in_progress, completed, failed
    error_message = Column(Text, nullable=True)
    started_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    completed_at = Column(DateTime, nullable=True)
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
    job_execution_id = Column(Integer, nullable=True, index=True)  # Link to job execution


class JobExecution(Base):
    """Track execution history of cron jobs."""
    __tablename__ = "job_executions"
    
    id = Column(Integer, primary_key=True, index=True)
    job_name = Column(String(100), index=True)
    status = Column(String(20))  # pending, running, completed, failed
    started_at = Column(DateTime, nullable=False)
    completed_at = Column(DateTime, nullable=True)
    duration_seconds = Column(Integer, nullable=True)
    items_processed = Column(Integer, nullable=True)
    items_failed = Column(Integer, nullable=True)
    error_message = Column(Text, nullable=True)
    result_summary = Column(Text, nullable=True)
    created_at = Column(DateTime, server_default=func.now())


class JobLock(Base):
    """Prevent concurrent execution of jobs."""
    __tablename__ = "job_locks"
    
    id = Column(Integer, primary_key=True, index=True)
    job_name = Column(String(100), unique=True)
    locked_at = Column(DateTime, nullable=False)
    locked_by = Column(String(100), nullable=True)
    expires_at = Column(DateTime, nullable=False)
    
    __table_args__ = (
        UniqueConstraint('job_name', name='uq_job_locks_job_name'),
    )


class EloCache(Base):
    """Persist Elo ratings for faster initialization."""
    __tablename__ = "elo_cache"
    
    id = Column(Integer, primary_key=True, index=True)
    team_name = Column(String(100), unique=True)
    rating = Column(Float, nullable=False)
    games_played = Column(Integer, default=0)
    last_updated = Column(DateTime, nullable=False)
    season = Column(Integer, nullable=False)
    
    __table_args__ = (
        UniqueConstraint('team_name', name='uq_elo_cache_team_name'),
    )
