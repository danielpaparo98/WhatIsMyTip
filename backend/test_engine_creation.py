"""Test database engine creation directly."""
import sys
import os
import logging

# Configure logging to see all diagnostic messages
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'backend'))

# Import the database module
from packages.shared import db

print("Testing database engine creation...")
print("=" * 60)

try:
    engine = db.get_engine()
    print("SUCCESS: Engine created successfully!")
    print(f"Driver: {engine.driver}")
    print(f"URL: {engine.url}")
except Exception as e:
    print(f"FAILURE: Engine creation failed!")
    print(f"Error: {e}")
    import traceback
    print("\nFull traceback:")
    traceback.print_exc()

print("=" * 60)