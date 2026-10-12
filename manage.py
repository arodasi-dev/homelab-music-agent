"""Administração das contas, pelo terminal do servidor.

    docker compose exec yt-music-agent python manage.py add-user maria
    docker compose exec yt-music-agent python manage.py list-users
"""
import argparse
import os
import shutil
import sys
from pathlib import Path

from accounts import DEFAULT_LIMIT, Accounts, AccountError


def parse_args(argv):
    parser = argparse.ArgumentParser(description="Contas do yt-music-agent")
    commands = parser.add_subparsers(dest="command", required=True)
    add = commands.add_parser("add-user", help="cria uma conta e mostra o token (uma vez só)")
    add.add_argument("name")
    add.add_argument("--limit", type=int, default=DEFAULT_LIMIT, help=f"máximo de músicas (padrão {DEFAULT_LIMIT})")
    commands.add_parser("list-users", help="lista as contas, quantas músicas e o limite")
    limit = commands.add_parser("set-limit", help="muda o limite de músicas de uma conta")
    limit.add_argument("name")
    limit.add_argument("limit", type=int)
    reset = commands.add_parser("reset-token", help="gera um token novo (o antigo deixa de valer)")
    reset.add_argument("name")
    remove = commands.add_parser("remove-user", help="apaga a conta e as músicas que só ela tinha")
    remove.add_argument("name")
    return parser.parse_args(argv)


def run(args, data_dir):
    accounts = Accounts(data_dir / "users.json")
    if not accounts.exists():
        raise AccountError("Ainda não há contas. Suba o agente uma vez (docker compose up -d) e tente de novo.")
    if args.command == "add-user":
        token = accounts.create(args.name, args.limit)
        print(f"Conta '{args.name}' criada (limite de {args.limit} músicas).")
        print(f"Token (mostrado só agora, entregue pra pessoa): {token}")
    elif args.command == "list-users":
        for user in accounts.summary():
            print(f"{user['name']:<20} {user['count']:>4} / {user['limit']} músicas")
    elif args.command == "set-limit":
        accounts.set_limit(args.name, args.limit)
        print(f"Limite de '{args.name}' agora é {args.limit}.")
    elif args.command == "reset-token":
        print(f"Token novo de '{args.name}' (mostrado só agora): {accounts.reset_token(args.name)}")
    elif args.command == "remove-user":
        removed = 0
        for track_id in accounts.remove(args.name):
            if accounts.is_orphan(track_id):
                shutil.rmtree(data_dir / "tracks" / track_id, ignore_errors=True)
                removed += 1
        print(f"Conta '{args.name}' apagada ({removed} músicas removidas do disco).")


def main(argv=None):
    try:
        run(parse_args(argv), Path(os.environ.get("DATA_DIR", "/data")))
    except AccountError as exc:
        sys.exit(str(exc))


if __name__ == "__main__":
    main()
