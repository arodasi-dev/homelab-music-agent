# yt-music-agent

Baixa o áudio de vídeos do YouTube (`yt-dlp` + `ffmpeg`) e serve pro app `yt-music-app` tocar.
Escuta só em `127.0.0.1:8091`; a tailnet chega nele via `tailscale serve`.

## API (tudo com `Authorization: Bearer $MUSIC_TOKEN`)
| Rota | O que faz |
|---|---|
| `GET /search?q=...` | Busca no YouTube (10 resultados: id, título, canal, duração, miniatura) |
| `GET /tracks` | Lista a biblioteca (`status`: `downloading`, `ready` ou `error`) |
| `POST /tracks` `{"url": "..."}` | Adiciona um link e baixa em segundo plano |
| `DELETE /tracks/<id>` | Apaga a faixa do disco |
| `GET /tracks/<id>/audio` | Áudio m4a (com `Range`, que o iPhone exige) |
| `GET /tracks/<id>/thumb` | Capa em jpg |

`audio` e `thumb` também aceitam o token em `?t=...`, porque `<audio>` e `<img>` não mandam header.

## Configuração
1. `cp .env.example .env` e preencha `MUSIC_TOKEN` (`openssl rand -hex 24`).
2. `ALLOWED_ORIGIN` já vem com `https://arodasi-dev.github.io`.

## Rodar no servidor
```
docker compose up -d --build
tailscale serve --bg --https=8444 http://127.0.0.1:8091
tailscale serve status
```
Se a 8444 estiver ocupada, use outra porta. É `serve` (só tailnet), **não** `funnel`.

URL pro app: `https://<nome-do-notebook>.<tailnet>.ts.net:8444`

## Manutenção
- **O YouTube muda e o `yt-dlp` quebra de tempos em tempos.** Se os downloads começarem a falhar,
  suba a versão em `requirements.txt` e rode `docker compose up -d --build`.
- Os arquivos ficam no volume do Docker `music-data` (`/data/<id-do-video>/` dentro do container).
  Pra ver o tamanho: `docker system df -v`. Pra limpar tudo: `docker compose down -v` (apaga a biblioteca inteira).

## Teste rápido
```
curl -H "Authorization: Bearer $MUSIC_TOKEN" http://127.0.0.1:8091/tracks
```
Testes: `pip install -r requirements-dev.txt && pytest`.

## Limites conhecidos
- Vídeos com mais de 3h são recusados.
- Só links de `youtube.com`, `youtu.be` e `music.youtube.com`; playlist inteira não (só o vídeo do link).
- Uso pessoal: baixar do YouTube vai contra os Termos de Serviço deles.
