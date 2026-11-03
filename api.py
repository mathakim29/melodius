# /// script
# requires-python = "==3.11.*"
# dependencies = [
#     "fastapi[standard]",
#     "uvicorn",
#     "torch",
#     "yt-dlp",
#     "transformers<=4.44.2",
#     "accelerate",
#     "torchaudio",
#     "mir_eval",
#     "pretty_midi",
#     "midi2audio",
#     "python-multipart",
#     "uvloop>=0.23.0",
# ]
# ///


import os
import hashlib
import time
from datetime import datetime, timezone
import shutil
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path

import torch
import uvicorn
import yt_dlp
from fastapi import FastAPI, Form, File, UploadFile, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from midi2audio import FluidSynth
from transformers import AutoModel

from fastapi.middleware.cors import CORSMiddleware

MODEL = None
DEVICE = "cuda" if torch.cuda.is_available() else "cpu"
OUTPUT_DIR = Path("output")
OUTPUT_DIR.mkdir(exist_ok=True)

@asynccontextmanager
async def lifespan(app: FastAPI):
    yield

app = FastAPI(lifespan=lifespan)
app.mount("/output", StaticFiles(directory="output"), name="static")

cors_origins = [
    origin.strip()
    for origin in os.environ.get(
        "CORS_ORIGINS",
        "http://localhost:3000,http://localhost:5173,http://localhost:8100,http://127.0.0.1:3000,http://127.0.0.1:5173,http://127.0.0.1:8100",
    ).split(",")
    if origin.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health():
    """Return a lightweight liveness response without loading the model."""
    return {"status": "ok"}

def make_job_id(source: str) -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S%f")[:-3]
    digest = hashlib.sha256(f"{source}:{timestamp}:{time.time_ns()}".encode()).digest()
    alphabet = "0123456789abcdefghijklmnopqrstuvwxyz"
    value = int.from_bytes(digest[:8], "big")
    hashid = ""
    while value:
        value, digit = divmod(value, len(alphabet))
        hashid = alphabet[digit] + hashid
    return f"{hashid[-10:] or '0'}-{timestamp}"


def get_model():
    """Load the transcription model only when the first job needs it."""
    global MODEL
    if MODEL is None:
        MODEL = AutoModel.from_pretrained(
            "m-a-p/SheetSage2",
            trust_remote_code=True,
        ).eval().to(DEVICE)
    return MODEL


def download_audio(url: str, output_path: str) -> str:
    ydl_opts = {
        'format': 'bestaudio/best',
        'postprocessors': [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'wav',
            'preferredquality': '192',
        }],
        'outtmpl': os.path.splitext(output_path)[0],
        'quiet': True,
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.download([url])
    return output_path


@app.post("/transcribe")
def transcribe(url: str = Form(...)):
    job_id = make_job_id(url)
    job_dir = OUTPUT_DIR / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp_dir:
        audio_path = os.path.join(tmp_dir, "song.wav")
        try:
            download_audio(url, audio_path)
            get_model().transcribe(audio_path, output_dir=str(job_dir))
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    mid_file = job_dir / "transcription.mid"
    if not mid_file.exists():
        raise HTTPException(status_code=500, detail="transcription.mid not found after transcription")

    return {"status": "success", "job_id": job_id, "file": f"/output/{job_id}/transcription.mid"}

if __name__ == "__main__":
    """Start the Stemsplitter API server."""
    import uvicorn
    uvicorn.run("api:app", host="127.0.0.1", port=int(os.environ.get("PORT", "8000")))
