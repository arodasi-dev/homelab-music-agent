"""Agente de música do homelab: baixa o áudio de vídeos do YouTube e serve pro app yt-music-app."""
import json
import os
import re
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

import yt_dlp
from flask import Flask, Response, abort, g, jsonify, request, send_file

from accounts import Accounts, LimitReached

YOUTUBE_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com", "youtu.be"}
VIDEO_ID = re.compile(r"^[\w-]{11}$")
MAX_DURATION_S = 3 * 3600
SEARCH_LIMIT = 10
MAX_QUERY_LENGTH = 100
MAX_PARALLEL_DOWNLOADS = 2
AUDIO_FORMAT = "bestaudio[ext=m4a]/bestaudio/best"
STREAM_TIMEOUT_S = 15
STREAM_CHUNK = 64 * 1024
PASSTHROUGH_HEADERS = ("Content-Type", "Content-Length", "Content-Range", "Accept-Ranges")
AUDIO_FILE = "audio.m4a"
THUMB_FILE = "audio.jpg"
META_FILE = "meta.json"
FASTSTART_MARKER = ".faststart"
FASTSTART_TIMEOUT_S = 120


def is_youtube_url(url):
    parsed = urlparse(url)
    return parsed.scheme in ("http", "https") and parsed.hostname in YOUTUBE_HOSTS


def ydl_options(**extra):
    return {"quiet": True, "no_warnings": True, "noplaylist": True, "cache_dir": "/tmp/yt-dlp", **extra}


def fetch_info(url):
    # Com o formato de áudio escolhido, o `info` já traz o endereço direto do áudio (`url`), usado no "ouvir baixando".
    with yt_dlp.YoutubeDL(ydl_options(format=AUDIO_FORMAT)) as ydl:
        return ydl.extract_info(url, download=False)


def open_stream(stream, range_header):
    """Abre o áudio direto no YouTube (repassando o `Range` do player) pra tocar antes do download terminar."""
    headers = dict(stream["headers"])
    if range_header:
        headers["Range"] = range_header
    return urlopen(Request(stream["url"], headers=headers), timeout=STREAM_TIMEOUT_S)


def stream_response(upstream):
    def chunks():
        try:
            while data := upstream.read(STREAM_CHUNK):
                yield data
        finally:
            upstream.close()

    headers = {k: upstream.headers[k] for k in PASSTHROUGH_HEADERS if upstream.headers.get(k)}
    return Response(chunks(), status=upstream.status, headers=headers)


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
        format=AUDIO_FORMAT,
        outtmpl=str(dest / "audio.%(ext)s"),
        writethumbnail=True,
        postprocessors=[
            {"key": "FFmpegExtractAudio", "preferredcodec": "m4a"},
            {"key": "FFmpegThumbnailsConvertor", "format": "jpg"},
        ],
    )
    with yt_dlp.YoutubeDL(options) as ydl:
        ydl.download([url])
    faststart(dest / AUDIO_FILE)


def faststart(path):
    """Move o índice do m4a pro começo do arquivo: sem isso o player precisa buscar o fim antes de tocar (demora)."""
    tmp = path.with_name("faststart.m4a")
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-i", str(path), "-c", "copy", "-movflags", "+faststart", str(tmp)],
            check=True, timeout=FASTSTART_TIMEOUT_S,
        )
        tmp.replace(path)
    except (OSError, subprocess.SubprocessError):  # o áudio continua tocando, só começa mais devagar
        tmp.unlink(missing_ok=True)


def faststart_existing(root):
    """Conserta as faixas baixadas antes do `faststart` (roda uma vez no fundo ao subir o servidor)."""
    for folder in root.iterdir():
        path = folder / AUDIO_FILE
        marker = folder / FASTSTART_MARKER
        if path.is_file() and not marker.exists():
            faststart(path)
            try:
                marker.touch()
            except OSError:  # a faixa foi apagada no meio
                pass


class Library:
    """Os arquivos de todo mundo: uma pasta por vídeo em `data/tracks/<video_id>/` com áudio, capa e meta.json.

    Quem tem cada faixa fica em `Accounts`; um vídeo só é baixado e guardado uma vez, mesmo com vários donos.
    """

    def __init__(self, data_dir):
        data_dir = Path(data_dir)
        self.root = data_dir / "tracks"
        self.root.mkdir(parents=True, exist_ok=True)
        self._adopt_legacy_folders(data_dir)
        self._fail_interrupted()

    def _adopt_legacy_folders(self, data_dir):
        # Antes das contas, as pastas ficavam direto em `data/`.
        for old in data_dir.iterdir():
            if old.is_dir() and VIDEO_ID.match(old.name) and (old / META_FILE).exists():
                old.rename(self.root / old.name)

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
        if not folder.is_dir():
            return
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


def create_app(token, allowed_origin, data_dir, executor=None, admin_name="admin"):
    """`token` e `admin_name` só criam a primeira conta (de quem administra); as outras vêm do `manage.py`."""
    app = Flask(__name__)
    library = Library(data_dir)
    accounts = Accounts(Path(data_dir) / "users.json")
    accounts.bootstrap(admin_name, token, {m["id"]: m["added"] for m in library.list()})
    executor = executor or ThreadPoolExecutor(max_workers=MAX_PARALLEL_DOWNLOADS)
    add_lock = threading.Lock()  # duas pessoas adicionando o mesmo vídeo ao mesmo tempo não baixam duas vezes
    streams = {}  # id -> endereço direto do áudio; só vale pra faixas adicionadas desde que o servidor subiu

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
        g.user = accounts.authenticate(sent)
        if not g.user:
            abort(401)

    def track_or_404(track_id):
        """A faixa só existe pra quem a tem na própria lista."""
        if not VIDEO_ID.match(track_id) or not accounts.has_track(g.user, track_id):
            abort(404)
        meta = library.read(track_id)
        if not meta:
            abort(404)
        return meta

    def file_or_404(track_id, name, mimetype):
        track_or_404(track_id)
        path = library.folder(track_id) / name
        if not path.is_file():
            abort(404)
        return send_file(path, mimetype=mimetype, conditional=True, max_age=86400)

    def with_added(meta):
        return {**meta, "added": accounts.tracks(g.user)[meta["id"]]}  # "quando" é por pessoa

    @app.get("/me")
    def me():
        return jsonify(accounts.info(g.user))

    @app.get("/tracks")
    def list_tracks():
        mine = accounts.tracks(g.user)
        metas = (library.read(track_id) for track_id in mine)
        return jsonify(sorted(({**m, "added": mine[m["id"]]} for m in metas if m), key=lambda m: m["added"], reverse=True))

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
        with add_lock:
            try:
                accounts.add_track(g.user, track_id)
            except LimitReached as exc:
                return jsonify(error=str(exc)), 403
            existing = library.read(track_id)
            if existing and existing["status"] != "error":
                return jsonify(with_added(existing)), 200  # já baixado (ou baixando) por alguém: só entra na lista
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
            if info.get("url"):
                streams[track_id] = {"url": info["url"], "headers": info.get("http_headers") or {}}
            executor.submit(run_download, library, track_id, f"https://www.youtube.com/watch?v={track_id}")
        return jsonify(with_added(meta)), 202

    @app.delete("/tracks/<track_id>")
    def delete_track(track_id):
        track_or_404(track_id)
        if accounts.remove_track(g.user, track_id):  # era o último dono: some do disco
            library.delete(track_id)
            streams.pop(track_id, None)
        return "", 204

    @app.get("/tracks/<track_id>/audio")
    def audio(track_id):
        if request.args.get("live"):  # tocando enquanto baixa: o arquivo ainda não está pronto
            track_or_404(track_id)
            stream = streams.get(track_id)
            if not stream:
                abort(404)
            try:
                return stream_response(open_stream(stream, request.headers.get("Range")))
            except HTTPError as exc:
                abort(exc.code if exc.code == 416 else 502)
            except OSError:
                abort(502)
        return file_or_404(track_id, AUDIO_FILE, "audio/mp4")

    @app.get("/tracks/<track_id>/thumb")
    def thumb(track_id):
        return file_or_404(track_id, THUMB_FILE, "image/jpeg")

    @app.get("/healthz")
    def healthz():
        return "ok"

    return app


def create_app_from_env():
    data_dir = os.environ.get("DATA_DIR", "/data")
    app = create_app(
        token=os.environ["MUSIC_TOKEN"],
        allowed_origin=os.environ["ALLOWED_ORIGIN"],
        data_dir=data_dir,
        admin_name=os.environ.get("ADMIN_NAME", "admin"),
    )
    threading.Thread(target=faststart_existing, args=(Path(data_dir) / "tracks",), daemon=True).start()
    return app
