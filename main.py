import json
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from agent import InvalidRepoUrl, stream_onboarding_doc

app = FastAPI(title="Codebase Onboarder")

STATIC_DIR = Path(__file__).resolve().parent
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


class OnboardRequest(BaseModel):
    repo_url: str


@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.post("/api/onboard")
async def onboard(req: OnboardRequest):
    if not req.repo_url or not req.repo_url.strip():
        raise HTTPException(status_code=400, detail="repo_url is required")

    async def event_stream():
        try:
            async for event in stream_onboarding_doc(req.repo_url):
                yield json.dumps(event) + "\n"
        except InvalidRepoUrl as e:
            yield json.dumps({"type": "error", "detail": str(e)}) + "\n"
        except Exception as e:  # noqa: BLE001
            yield json.dumps({"type": "error", "detail": f"Unexpected error: {e}"}) + "\n"

    return StreamingResponse(event_stream(), media_type="application/x-ndjson")