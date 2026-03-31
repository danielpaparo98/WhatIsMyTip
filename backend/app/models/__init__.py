from sqlalchemy import Column, Integer, String, Float, DateTime, Text, Boolean, UniqueConstraint
from sqlalchemy.sql import func
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
