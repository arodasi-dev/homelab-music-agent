"""Agente de música do homelab: baixa o áudio de vídeos do YouTube e serve pro app yt-music-app."""
import hmac
import json
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlparse

import yt_dlp
from flask import Flask, abort, jsonify, request, send_file

YOUTUBE_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com", "youtu.be"}
VIDEO_ID = re.compile(r"^[\w-]{11}$")
MAX_DURATION_S = 3 * 3600
SEARCH_LIMIT = 10
MAX_QUERY_LENGTH = 100
MAX_PARALLEL_DOWNLOADS = 2
AUDIO_FILE = "audio.m4a"
THUMB_FILE = "audio.jpg"
META_FILE = "meta.json"


def is_youtube_url(url):
    parsed = urlparse(url)
    return parsed.scheme in ("http", "https") and parsed.hostname in YOUTUBE_HOSTS


def ydl_options(**extra):
    return {"quiet": True, "no_warnings": True, "noplaylist": True, "cache_dir": "/tmp/yt-dlp", **extra}


def fetch_info(url):
    with yt_dlp.YoutubeDL(ydl_options()) as ydl:
        return ydl.extract_info(url, download=False)


def search_videos(query):
    """Primeiros resultados da busca do YouTube (só a lista, sem baixar nada)."""
    with yt_dlp.YoutubeDL(ydl_options(extract_flat=True)) as ydl:
        info = ydl.extract_info(f"ytsearch{SEARCH_LIMIT}:{query}", download=False)
    return [
        {
            "id": entry["id"],
            "title": entry.get("title") or entry["id"],
            "channel": entry.get("channel") or entry.get("uploader") or "",
            "duration": entry.get("duration") or 0,
            "thumbnail": f"https://i.ytimg.com/vi/{entry['id']}/mqdefault.jpg",
        }
        for entry in info.get("entries", [])
        if VIDEO_ID.match(entry.get("id") or "")
    ]


def download_audio(url, dest):
    options = ydl_options(
        format="bestaudio[ext=m4a]/bestaudio/best",
        outtmpl=str(dest / "audio.%(ext)s"),
        writethumbnail=True,
        postprocessors=[
            {"key": "FFmpegExtractAudio", "preferredcodec": "m4a"},
            {"key": "FFmpegThumbnailsConvertor", "format": "jpg"},
        ],
    )
    with yt_dlp.YoutubeDL(options) as ydl:
        ydl.download([url])


class Library:
    """Uma pasta por vídeo em `root/<video_id>/` com o áudio, a capa e um meta.json."""

    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._fail_interrupted()

    def folder(self, track_id):
        return self.root / track_id

    def read(self, track_id):
        try:
            return json.loads((self.folder(track_id) / META_FILE).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def write(self, track_id, meta):
        folder = self.folder(track_id)
        folder.mkdir(parents=True, exist_ok=True)
        tmp = folder / f"{META_FILE}.tmp"
        tmp.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
        tmp.replace(folder / META_FILE)

    def list(self):
        tracks = [m for m in (self.read(p.name) for p in self.root.iterdir() if p.is_dir()) if m]
        return sorted(tracks, key=lambda m: m["added"], reverse=True)

    def delete(self, track_id):
        folder = self.folder(track_id)
        for item in folder.iterdir():
            item.unlink()
        folder.rmdir()

    def _fail_interrupted(self):
        # Download que estava rodando quando o container caiu: marca como erro pra dar pra tentar de novo.
        for meta in self.list():
            if meta["status"] == "downloading":
                self.write(meta["id"], {**meta, "status": "error", "error": "Download interrompido"})


def run_download(library, track_id, url):
    meta = library.read(track_id)
    try:
        download_audio(url, library.folder(track_id))
        library.write(track_id, {**meta, "status": "ready", "error": None})
    except Exception as exc:  # o yt-dlp lança vários tipos; o app só precisa da mensagem
        library.write(track_id, {**meta, "status": "error", "error": str(exc)[:200]})


def create_app(token, allowed_origin, data_dir, executor=None):
    app = Flask(__name__)
    library = Library(data_dir)
    executor = executor or ThreadPoolExecutor(max_workers=MAX_PARALLEL_DOWNLOADS)

    @app.after_request
    def cors(response):
        if request.headers.get("Origin") == allowed_origin:
            response.headers["Access-Control-Allow-Origin"] = allowed_origin
            response.headers["Access-Control-Allow-Headers"] = "Authorization, Content-Type"
            response.headers["Access-Control-Allow-Methods"] = "GET, POST, DELETE, OPTIONS"
            response.headers["Vary"] = "Origin"
        return response

    @app.before_request
    def require_token():
        if request.method == "OPTIONS" or request.endpoint == "healthz":
            return
        # <audio> e <img> não mandam header, então o app também aceita o token em `?t=`.
        sent = request.headers.get("Authorization", "").removeprefix("Bearer ") or request.args.get("t", "")
        if not hmac.compare_digest(sent, token):
            abort(401)

    def track_or_404(track_id):
        meta = VIDEO_ID.match(track_id) and library.read(track_id)
        if not meta:
            abort(404)
        return meta

    def file_or_404(track_id, name, mimetype):
        track_or_404(track_id)
        path = library.folder(track_id) / name
        if not path.is_file():
            abort(404)
        return send_file(path, mimetype=mimetype, conditional=True, max_age=86400)

    @app.get("/tracks")
    def list_tracks():
        return jsonify(library.list())

    @app.get("/search")
    def search():
        query = request.args.get("q", "").strip()
        if not query or len(query) > MAX_QUERY_LENGTH:
            return jsonify(error="Digite o que buscar."), 400
        try:
            return jsonify(search_videos(query))
        except yt_dlp.utils.DownloadError:
            return jsonify(error="A busca falhou. Tente de novo."), 502

    @app.post("/tracks")
    def add_track():
        url = (request.get_json(silent=True) or {}).get("url", "").strip()
        if not is_youtube_url(url):
            return jsonify(error="Cole um link do YouTube."), 400
        try:
            info = fetch_info(url)
        except yt_dlp.utils.DownloadError:
            return jsonify(error="Não consegui abrir esse vídeo."), 422
        track_id = info.get("id", "")
        if not VIDEO_ID.match(track_id):
            return jsonify(error="Esse link não é de um vídeo."), 422
        if (info.get("duration") or 0) > MAX_DURATION_S:
            return jsonify(error="Vídeo longo demais (máximo 3h)."), 422
        existing = library.read(track_id)
        if existing and existing["status"] != "error":
            return jsonify(existing), 200
        meta = {
            "id": track_id,
            "title": info.get("title") or track_id,
            "channel": info.get("channel") or info.get("uploader") or "",
            "duration": info.get("duration") or 0,
            "status": "downloading",
            "error": None,
            "added": time.time(),
        }
        library.write(track_id, meta)
        executor.submit(run_download, library, track_id, f"https://www.youtube.com/watch?v={track_id}")
        return jsonify(meta), 202

    @app.delete("/tracks/<track_id>")
    def delete_track(track_id):
        track_or_404(track_id)
        library.delete(track_id)
        return "", 204

    @app.get("/tracks/<track_id>/audio")
    def audio(track_id):
        return file_or_404(track_id, AUDIO_FILE, "audio/mp4")

    @app.get("/tracks/<track_id>/thumb")
    def thumb(track_id):
        return file_or_404(track_id, THUMB_FILE, "image/jpeg")

    @app.get("/healthz")
    def healthz():
        return "ok"

    return app


def create_app_from_env():
    return create_app(
        token=os.environ["MUSIC_TOKEN"],
        allowed_origin=os.environ["ALLOWED_ORIGIN"],
        data_dir=os.environ.get("DATA_DIR", "/data"),
    )
