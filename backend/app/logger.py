import logging
import sys
from app.config import settings


def get_logger(name: str) -> logging.Logger:
    """Get a configured logger instance.
    
    Args:
        name: The name for the logger (typically __name__)
        
    Returns:
        A configured logger instance
    """
    logger = logging.getLogger(name)
    
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        formatter = logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
        )
        handler.setFormatter(formatter)
        logger.addHandler(handler)
    
    logger.setLevel(logging.DEBUG if settings.environment == "development" else logging.INFO)
    return logger
