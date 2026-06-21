from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    Column,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from ..db import Base


class Game(Base):
    __tablename__ = "games"

    id = Column(Integer, primary_key=True, index=True)
    slug = Column(String(12), unique=True, index=True, nullable=False)
    squiggle_id = Column(Integer, unique=True, index=True)
    afltables_match_id = Column(Text, unique=True, index=True, nullable=True)
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

    __table_args__ = (UniqueConstraint("game_id", "heuristic", name="uq_game_heuristic"),)


class ModelPrediction(Base):
    __tablename__ = "model_predictions"

    id = Column(Integer, primary_key=True, index=True)
    game_id = Column(Integer, index=True)
    model_name = Column(String(50), index=True)  # elo, form, home_advantage, value
    winner = Column(String(100))
    confidence = Column(Float)
    margin = Column(Integer)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (UniqueConstraint("game_id", "model_name", name="uq_game_model"),)


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
        UniqueConstraint(
            "season", "round_id", "heuristic", name="uq_backtest_season_round_heuristic"
        ),
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
    started_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    completed_at = Column(DateTime(timezone=True), nullable=True)
    updated_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )
    job_execution_id = Column(Integer, nullable=True, index=True)  # Link to job execution


class JobExecution(Base):
    """Track execution history of cron jobs."""

    __tablename__ = "job_executions"

    id = Column(Integer, primary_key=True, index=True)
    job_name = Column(String(100), index=True)
    status = Column(String(20))  # pending, running, completed, failed
    started_at = Column(DateTime(timezone=True), nullable=False)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    duration_seconds = Column(Integer, nullable=True)
    items_processed = Column(Integer, nullable=True)
    items_failed = Column(Integer, nullable=True)
    error_message = Column(Text, nullable=True)
    result_summary = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class JobLock(Base):
    """Prevent concurrent execution of jobs."""

    __tablename__ = "job_locks"

    id = Column(Integer, primary_key=True, index=True)
    job_name = Column(String(100), unique=True)
    locked_at = Column(DateTime(timezone=True), nullable=False)
    locked_by = Column(String(100), nullable=True)
    expires_at = Column(DateTime(timezone=True), nullable=False)

    __table_args__ = (UniqueConstraint("job_name", name="uq_job_locks_job_name"),)


class EloCache(Base):
    """Persist Elo ratings for faster initialization."""

    __tablename__ = "elo_cache"

    id = Column(Integer, primary_key=True, index=True)
    team_name = Column(String(100), unique=True)
    rating = Column(Float, nullable=False)
    games_played = Column(Integer, default=0)
    last_updated = Column(DateTime(timezone=True), nullable=False)
    season = Column(Integer, nullable=False)

    __table_args__ = (UniqueConstraint("team_name", name="uq_elo_cache_team_name"),)


class MatchAnalysis(Base):
    __tablename__ = "match_analyses"

    id = Column(Integer, primary_key=True, index=True)
    game_id = Column(Integer, ForeignKey("games.id"), unique=True, nullable=False, index=True)
    analysis_text = Column(Text, nullable=False)  # Balanced talking points for the match
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    game = relationship("Game", backref="match_analysis")


class Player(Base):
    __tablename__ = "players"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(Text, unique=True, nullable=False, index=True)
    afltables_id = Column(Text, unique=True, nullable=True, index=True)
    footywire_id = Column(Integer, nullable=True, index=True)
    current_team = Column(Text, nullable=True, index=True)
    position = Column(Text, nullable=True)
    height = Column(Text, nullable=True)
    weight = Column(Text, nullable=True)
    date_of_birth = Column(Date, nullable=True)
    draft_info = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())


class MatchWeather(Base):
    __tablename__ = "match_weather"

    id = Column(Integer, primary_key=True, index=True)
    game_id = Column(Integer, ForeignKey("games.id"), unique=True, nullable=False, index=True)
    venue = Column(Text, nullable=True, index=True)
    match_date = Column(Date, nullable=True)
    temperature = Column(Float, nullable=True)
    precipitation = Column(Float, nullable=True)
    wind_speed = Column(Float, nullable=True)
    wind_direction = Column(Integer, nullable=True)
    wind_gusts = Column(Float, nullable=True)
    humidity = Column(Integer, nullable=True)
    weather_code = Column(Integer, nullable=True)
    data_type = Column(Text, nullable=True, default="historical", index=True)
    raw_hourly = Column(JSONB, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    game = relationship("Game", backref="weather")


class PlayerMatchStats(Base):
    __tablename__ = "player_match_stats"

    id = Column(Integer, primary_key=True, index=True)
    game_id = Column(Integer, ForeignKey("games.id"), nullable=False, index=True)
    player_id = Column(Integer, ForeignKey("players.id"), nullable=False, index=True)
    team = Column(Text, nullable=True, index=True)
    kicks = Column(Integer, default=0)
    handballs = Column(Integer, default=0)
    disposals = Column(Integer, default=0)
    marks = Column(Integer, default=0)
    goals = Column(Integer, default=0)
    behinds = Column(Integer, default=0)
    tackles = Column(Integer, default=0)
    hitouts = Column(Integer, default=0)
    frees_for = Column(Integer, default=0)
    frees_against = Column(Integer, default=0)

    __table_args__ = (UniqueConstraint("game_id", "player_id", name="uq_pms_game_player"),)

    game = relationship("Game", backref="player_stats")
    player = relationship("Player", backref="match_stats")


class PlayerAdvancedStats(Base):
    __tablename__ = "player_advanced_stats"

    id = Column(Integer, primary_key=True, index=True)
    game_id = Column(Integer, ForeignKey("games.id"), nullable=False, index=True)
    player_id = Column(Integer, ForeignKey("players.id"), nullable=False, index=True)
    round_label = Column(Text, nullable=True)
    opponent = Column(Text, nullable=True)
    tog_pct = Column(Float, nullable=True)
    metres_gained = Column(Integer, nullable=True)
    score_involvements = Column(Integer, nullable=True)
    contested_possessions = Column(Integer, nullable=True)
    pressure_acts = Column(Integer, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (UniqueConstraint("game_id", "player_id", name="uq_pas_game_player"),)

    game = relationship("Game", backref="advanced_stats")
    player = relationship("Player", backref="advanced_stats")


class Injury(Base):
    __tablename__ = "injuries"

    id = Column(Integer, primary_key=True, index=True)
    player_id = Column(Integer, ForeignKey("players.id"), nullable=True, index=True)
    player_name = Column(Text, nullable=False)
    team = Column(Text, nullable=True, index=True)
    injury_type = Column(Text, nullable=True)
    return_timeline = Column(Text, nullable=True)
    source = Column(Text, default="footywire")
    scraped_at = Column(DateTime(timezone=True), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    __table_args__ = (
        UniqueConstraint("player_name", "injury_type", name="uq_injuries_player_injury"),
    )

    player = relationship("Player", backref="injuries")
