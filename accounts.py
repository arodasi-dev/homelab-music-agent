"""Contas do agente de música: quem pode entrar (token pessoal) e a lista de faixas de cada pessoa."""
import hashlib
import hmac
import json
import re
import secrets
import threading
import time
from pathlib import Path

NAME = re.compile(r"^[a-z0-9_-]{2,32}$")
DEFAULT_LIMIT = 200


class AccountError(Exception):
    """Erro de regra de negócio, com mensagem pronta pra mostrar a quem administra ou usa."""


class LimitReached(AccountError):
    pass


def hash_token(token):
    return hashlib.sha256(token.encode()).hexdigest()


class Accounts:
    """`users.json`: {nome: {token_hash, limit, created, tracks: {id_do_video: quando_adicionou}}}.

    O arquivo é pequeno e relido a cada operação, então o servidor e o comando `manage.py`
    enxergam as mudanças um do outro sem precisar reiniciar.
    """

    def __init__(self, path):
        self.path = Path(path)
        self._lock = threading.Lock()

    def _load(self):
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}

    def _save(self, users):
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(json.dumps(users, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(self.path)

    def exists(self):
        return self.path.exists()

    def bootstrap(self, name, token, tracks):
        """Primeira subida: a conta de quem administra herda o token antigo e as faixas que já existiam."""
        with self._lock:
            if self.path.exists():
                return
            self._save({name: {
                "token_hash": hash_token(token),
                "limit": DEFAULT_LIMIT,
                "created": time.time(),
                "tracks": tracks,
            }})

    def authenticate(self, token):
        if not token:
            return None
        sent = hash_token(token)
        with self._lock:
            users = self._load()
        for name, user in users.items():
            if hmac.compare_digest(user["token_hash"], sent):
                return name
        return None

    def create(self, name, limit=DEFAULT_LIMIT):
        if not NAME.match(name):
            raise AccountError("Nome inválido: use 2 a 32 letras minúsculas, números, - ou _.")
        token = secrets.token_hex(24)
        with self._lock:
            users = self._load()
            if name in users:
                raise AccountError(f"A conta '{name}' já existe.")
            users[name] = {"token_hash": hash_token(token), "limit": limit, "created": time.time(), "tracks": {}}
            self._save(users)
        return token

    def reset_token(self, name):
        token = secrets.token_hex(24)
        with self._lock:
            users = self._load()
            self._get(users, name)["token_hash"] = hash_token(token)
            self._save(users)
        return token

    def set_limit(self, name, limit):
        with self._lock:
            users = self._load()
            self._get(users, name)["limit"] = limit
            self._save(users)

    def remove(self, name):
        """Apaga a conta e devolve os ids de vídeo que eram dela (pra quem chamou limpar os órfãos)."""
        with self._lock:
            users = self._load()
            tracks = list(self._get(users, name)["tracks"])
            del users[name]
            self._save(users)
        return tracks

    def summary(self):
        with self._lock:
            users = self._load()
        return [{"name": n, "count": len(u["tracks"]), "limit": u["limit"]} for n, u in sorted(users.items())]

    def info(self, name):
        with self._lock:
            user = self._get(self._load(), name)
        return {"name": name, "count": len(user["tracks"]), "limit": user["limit"]}

    def tracks(self, name):
        with self._lock:
            return dict(self._get(self._load(), name)["tracks"])

    def has_track(self, name, track_id):
        return track_id in self.tracks(name)

    def add_track(self, name, track_id):
        """Põe o vídeo na lista da pessoa. Não conta de novo se ela já tinha."""
        with self._lock:
            users = self._load()
            user = self._get(users, name)
            if track_id in user["tracks"]:
                return
            if len(user["tracks"]) >= user["limit"]:
                raise LimitReached(f"Limite de {user['limit']} músicas atingido. Apague alguma pra adicionar.")
            user["tracks"][track_id] = time.time()
            self._save(users)

    def remove_track(self, name, track_id):
        """Tira da lista da pessoa. Devolve True se mais ninguém tem o vídeo (aí o arquivo pode ser apagado)."""
        with self._lock:
            users = self._load()
            users[name]["tracks"].pop(track_id, None)
            self._save(users)
            return not any(track_id in u["tracks"] for u in users.values())

    def is_orphan(self, track_id):
        with self._lock:
            return not any(track_id in u["tracks"] for u in self._load().values())

    @staticmethod
    def _get(users, name):
        if name not in users:
            raise AccountError(f"Não existe a conta '{name}'.")
        return users[name]
