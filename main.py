
import os
import re
import shutil
import secrets
import tempfile

from pathlib import Path
from urllib.parse import urlparse
from typing import Literal

from fastapi import FastAPI, Header, HTTPException, Query
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask
from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadError

app = FastAPI(
    title="MC Download API",
    version="1.0.0",
)

API_KEY = os.getenv("MC_API_KEY", "")
MAX_FILE_BYTES = 300 * 1024 * 1024  # 300 MB

ALLOWED_HOSTS = {
    "youtube.com",
    "www.youtube.com",
    "m.youtube.com",
    "youtu.be",
    "tiktok.com",
    "www.tiktok.com",
    "m.tiktok.com",
    "vm.tiktok.com",
    "vt.tiktok.com",
}


def validate_url(url: str) -> None:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower().rstrip(".")

    if parsed.scheme != "https":
        raise HTTPException(
            status_code=400,
            detail="El enlace debe utilizar HTTPS.",
        )

    if host not in ALLOWED_HOSTS:
        raise HTTPException(
            status_code=400,
            detail="Solo se admiten enlaces de YouTube y TikTok.",
        )

    if parsed.username or parsed.password:
        raise HTTPException(
            status_code=400,
            detail="La URL no puede contener credenciales.",
        )


def check_api_key(x_api_key: str | None) -> None:
    if not API_KEY:
        raise HTTPException(
            status_code=500,
            detail="Falta configurar MC_API_KEY en el servidor.",
        )

    if not x_api_key or not secrets.compare_digest(
        x_api_key, API_KEY
    ):
        raise HTTPException(
            status_code=401,
            detail="Clave API incorrecta.",
        )


@app.get("/")
def root():
    return {
        "service": "MC Download API",
        "status": "online",
    }


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/download")
def download_media(
    url: str = Query(..., min_length=10, max_length=2048),
    format: Literal["audio", "video"] = Query(...),
    x_api_key: str | None = Header(default=None),
):
    check_api_key(x_api_key)
    validate_url(url)

    work_dir = Path(tempfile.mkdtemp(prefix="mcdownload_"))
    completed = False

    try:
        output_template = str(work_dir / "media.%(ext)s")

        options = {
            "outtmpl": output_template,
            "noplaylist": True,
            "restrictfilenames": True,
            "socket_timeout": 20,
            "retries": 2,
            "fragment_retries": 2,
            "max_filesize": MAX_FILE_BYTES,
            "noprogress": True,
            "quiet": True,
            "no_warnings": True,
            "js_runtimes": {"node": {}},
        }

        if format == "audio":
            options["format"] = "bestaudio/best"
            options["postprocessors"] = [{
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "192",
            }]
        else:
            options["format"] = (
                "bestvideo[ext=mp4]+bestaudio[ext=m4a]"
                "/best[ext=mp4]"
            )
            options["merge_output_format"] = "mp4"

        with YoutubeDL(options) as ydl:
            ydl.download([url])

        files = [
            p for p in work_dir.iterdir()
            if p.is_file() and p.stat().st_size > 0
        ]

        expected_ext = ".mp3" if format == "audio" else ".mp4"
        results = [
            p for p in files
            if p.suffix.lower() == expected_ext
        ]

        if len(results) != 1:
            raise HTTPException(
                status_code=502,
                detail=(
                    "No se obtuvo un archivo final compatible. "
                    "Prueba con otro enlace."
                ),
            )

        result = results[0]

        if result.stat().st_size > MAX_FILE_BYTES:
            raise HTTPException(
                status_code=413,
                detail="El archivo supera el límite de 300 MB.",
            )

        media_type = (
            "audio/mpeg" if format == "audio"
            else "video/mp4"
        )

        completed = True

        return FileResponse(
            path=str(result),
            media_type=media_type,
            filename=f"mc_download{expected_ext}",
            background=BackgroundTask(
                shutil.rmtree,
                str(work_dir),
                ignore_errors=True,
            ),
        )

    except HTTPException:
        raise

    except DownloadError as exc:
        raise HTTPException(
            status_code=422,
            detail=(
                "No se pudo procesar el enlace. Puede ser "
                "privado, estar restringido o no ser compatible."
            ),
        ) from exc

    except Exception as exc:
        print(f"Error procesando descarga: {exc}")
        raise HTTPException(
            status_code=500,
            detail="Error interno procesando la descarga.",
        ) from exc

    finally:
        if not completed:
            shutil.rmtree(work_dir, ignore_errors=True)
