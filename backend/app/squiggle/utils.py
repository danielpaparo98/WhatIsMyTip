"""Utility functions for Squiggle API data parsing."""


def parse_squiggle_complete(value) -> bool:
    """Parse Squiggle's complete status.
    
    Squiggle returns 100 for complete games instead of True/False.
    This function normalises the various possible representations.
    
    Args:
        value: The complete status value from Squiggle API.
            Can be int (100), bool, or str.
        
    Returns:
        True if the game is complete, False otherwise.
    """
    if isinstance(value, int):
        return value == 100
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.lower() in ("100", "true", "yes")
    return False
