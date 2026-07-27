from fastapi import FastAPI

app = FastAPI(title="TenderOS API")


@app.get("/health/live")
async def health_live() -> dict[str, str]:
    return {"status": "ok"}
