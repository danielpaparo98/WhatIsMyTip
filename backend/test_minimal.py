"""Minimal test to isolate the hanging issue."""
import asyncio
from fastapi import FastAPI

app = FastAPI(title="Test API")

@app.get("/")
async def root():
    return {"message": "Test API is working"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)
