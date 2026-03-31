from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession
from slowapi import Limiter
from slowapi.util import get_remote_address
from typing import Optional

from app.db import get_db
from app.crud import GameCRUD
from app.logger import get_logger


router = APIRouter()
limiter = Limiter(key_func=get_remote_address)
logger = get_logger(__name__)
