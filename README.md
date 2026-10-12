# yt-music-agent

Baixa o áudio de vídeos do YouTube (`yt-dlp` + `ffmpeg`) e serve pro app `yt-music-app` tocar.
Várias pessoas podem usar, cada uma com a própria conta e biblioteca.
Escuta só em `127.0.0.1:8091`; a tailnet chega nele via `tailscale serve`.

## Contas
- Cada pessoa tem um **token pessoal**; é ele que identifica a conta (o app pede na primeira abertura).
- Cada uma vê **só a própria biblioteca** e tem um **limite de músicas** (padrão 200).
- **O arquivo de áudio é guardado uma vez só**: se duas pessoas adicionam o mesmo vídeo, ele não baixa de novo.
  Quando alguém apaga, só sai da lista dela; o arquivo some do disco quando ninguém mais o tem.
- Os tokens ficam em `users.json` (volume `music-data`) **só como hash**: se perder um, gere outro (`reset-token`).

### Administrar (no servidor, dentro da pasta do projeto)
```
docker compose exec yt-music-agent python manage.py add-user maria            # cria e mostra o token (uma vez só)
docker compose exec yt-music-agent python manage.py add-user joao --limit 50
docker compose exec yt-music-agent python manage.py list-users                # contas, uso e limite
docker compose exec yt-music-agent python manage.py set-limit maria 300
docker compose exec yt-music-agent python manage.py reset-token maria         # o token antigo deixa de valer
docker compose exec yt-music-agent python manage.py remove-user maria         # apaga a conta e o que só ela tinha
```
Entregue o token pra pessoa por um canal privado. Ele aparece uma vez; não fica guardado em texto.

### Primeira subida com contas (quem já usava antes)
A conta de quem administra é criada sozinha, **uma vez**, a partir do `.env`:
o `MUSIC_TOKEN` atual continua valendo (o app não precisa mudar nada) e todas as músicas que já
existiam passam a ser dela. Defina `ADMIN_NAME` no `.env` **antes** de subir essa versão; depois que
`users.json` existe, mudar `ADMIN_NAME` ou `MUSIC_TOKEN` no `.env` não muda a conta.

## API (tudo com `Authorization: Bearer <token da conta>`)
| Rota | O que faz |
|---|---|
| `GET /me` | Nome da conta, quantas músicas tem e o limite |
| `GET /search?q=...` | Busca no YouTube (10 resultados: id, título, canal, duração, miniatura) |
| `GET /tracks` | Lista a biblioteca da conta (`status`: `downloading`, `ready` ou `error`) |
| `POST /tracks` `{"url": "..."}` | Adiciona um link à conta e baixa em segundo plano (`403` se passou do limite) |
| `DELETE /tracks/<id>` | Tira a faixa da conta (e do disco, se ninguém mais a tem) |
| `GET /tracks/<id>/audio` | Áudio m4a (com `Range`, que o iPhone exige); `?live=1` toca enquanto baixa |
| `GET /tracks/<id>/thumb` | Capa em jpg |

`audio` e `thumb` também aceitam o token em `?t=...`, porque `<audio>` e `<img>` não mandam header.
Faixa que não está na lista da conta responde `404`, mesmo que exista no disco.

## Configuração
1. `cp .env.example .env`, preencha `MUSIC_TOKEN` (`openssl rand -hex 24`) e `ADMIN_NAME`.
2. `ALLOWED_ORIGIN` já vem com `https://arodasi-dev.github.io`.

## Rodar no servidor
```
docker compose up -d --build
tailscale serve --bg --https=8444 http://127.0.0.1:8091
tailscale serve status
```
Se a 8444 estiver ocupada, use outra porta. É `serve` (só tailnet), **não** `funnel`.

URL pro app: `https://<nome-do-notebook>.<tailnet>.ts.net:8444`

### Dando acesso a outras pessoas
O agente só é alcançável pela tailnet, então quem vai usar precisa **estar na tailnet** (app do Tailscale instalado
e conectado). No painel do Tailscale, compartilhe o servidor com a pessoa (*Machines → o servidor → Share*) ou
convide ela pra tailnet. **Antes de convidar, restrinja o acesso dela** (ACL) só à porta 8444 desse servidor:
sem isso ela enxerga também as outras portas dele (SSH, health-agent, etc.).

## Manutenção
- **O YouTube muda e o `yt-dlp` quebra de tempos em tempos.** Se os downloads começarem a falhar,
  suba a versão em `requirements.txt` e rode `docker compose up -d --build`.
- Os arquivos ficam no volume do Docker `music-data` (`/data/tracks/<id-do-video>/` dentro do container).
  Pra ver o tamanho: `docker system df -v`. Pra limpar tudo: `docker compose down -v` (apaga a biblioteca e as contas).

## Teste rápido
```
curl -H "Authorization: Bearer $MUSIC_TOKEN" http://127.0.0.1:8091/tracks
```
Testes: `pip install -r requirements-dev.txt && pytest`.

## Limites conhecidos
- Vídeos com mais de 3h são recusados.
- Só links de `youtube.com`, `youtu.be` e `music.youtube.com`; playlist inteira não (só o vídeo do link).
- Baixar do YouTube vai contra os Termos de Serviço deles. Dividir o servidor com outras pessoas aumenta
  o uso e a exposição: mantenha só entre gente de confiança.
