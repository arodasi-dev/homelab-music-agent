from unittest.mock import patch

import pytest
import yt_dlp

import agent

ORIGIN = "https://arodasi-dev.github.io"
VIDEO = "dQw4w9WgXcQ"
URL = f"https://www.youtube.com/watch?v={VIDEO}"
INFO = {"id": VIDEO, "title": "Música", "channel": "Canal", "duration": 212}


class SyncExecutor:
    """Roda o download na hora, pra o teste não depender de thread."""

    def submit(self, fn, *args):
        fn(*args)


def fake_download(url, dest):
    (dest / agent.AUDIO_FILE).write_bytes(b"0123456789")
    (dest / agent.THUMB_FILE).write_bytes(b"jpg")


@pytest.fixture
def client(tmp_path):
    return agent.create_app("segredo", ORIGIN, tmp_path, executor=SyncExecutor()).test_client()


def auth(token="segredo"):
    return {"Authorization": f"Bearer {token}"}


def add(client, url=URL):
    return client.post("/tracks", json={"url": url}, headers=auth())


def test_sem_token_nega(client):
    assert client.get("/tracks").status_code == 401


def test_token_errado_nega(client):
    assert client.get("/tracks", headers=auth("errado")).status_code == 401


def test_healthz_nao_pede_token(client):
    assert client.get("/healthz").status_code == 200


def test_link_que_nao_e_youtube_nega(client):
    assert add(client, "https://example.com/video").status_code == 400
    assert add(client, "file:///etc/passwd").status_code == 400


def test_adiciona_baixa_e_lista(client):
    with patch.object(agent, "fetch_info", return_value=INFO), patch.object(agent, "download_audio", fake_download):
        res = add(client)
    assert res.status_code == 202
    [track] = client.get("/tracks", headers=auth()).get_json()
    assert track["id"] == VIDEO and track["status"] == "ready" and track["title"] == "Música"


def test_link_repetido_nao_baixa_de_novo(client):
    with patch.object(agent, "fetch_info", return_value=INFO), patch.object(agent, "download_audio", fake_download):
        add(client)
        with patch.object(agent, "download_audio") as again:
            res = add(client)
    assert res.status_code == 200
    again.assert_not_called()


def test_falha_no_download_vira_erro_e_da_pra_tentar_de_novo(client):
    with patch.object(agent, "fetch_info", return_value=INFO), \
         patch.object(agent, "download_audio", side_effect=RuntimeError("sem rede")):
        add(client)
        [track] = client.get("/tracks", headers=auth()).get_json()
        assert track["status"] == "error" and "sem rede" in track["error"]
    with patch.object(agent, "fetch_info", return_value=INFO), patch.object(agent, "download_audio", fake_download):
        assert add(client).status_code == 202
    assert client.get("/tracks", headers=auth()).get_json()[0]["status"] == "ready"


def test_video_inacessivel_devolve_422(client):
    with patch.object(agent, "fetch_info", side_effect=yt_dlp.utils.DownloadError("privado")):
        assert add(client).status_code == 422


def test_video_longo_demais_nega(client):
    with patch.object(agent, "fetch_info", return_value={**INFO, "duration": 4 * 3600}):
        assert add(client).status_code == 422


def test_audio_aceita_token_na_query_e_suporta_range(client):
    with patch.object(agent, "fetch_info", return_value=INFO), patch.object(agent, "download_audio", fake_download):
        add(client)
    assert client.get(f"/tracks/{VIDEO}/audio").status_code == 401
    res = client.get(f"/tracks/{VIDEO}/audio?t=segredo", headers={"Range": "bytes=2-5"})
    assert res.status_code == 206
    assert res.data == b"2345"


class FakeUpstream:
    """Resposta do YouTube: 206 com o trecho pedido."""

    status = 206
    headers = {"Content-Type": "audio/mp4", "Content-Range": "bytes 2-5/10", "Content-Length": "4", "Set-Cookie": "x"}

    def __init__(self):
        self.parts = [b"23", b"45"]
        self.closed = False

    def read(self, size):
        return self.parts.pop(0) if self.parts else b""

    def close(self):
        self.closed = True


def test_ouvir_baixando_repassa_o_range_do_youtube(client):
    info = {**INFO, "url": "https://rr1.googlevideo.example/audio", "http_headers": {"User-Agent": "yt"}}
    seen = {}

    def fake_urlopen(request, timeout):
        seen["url"], seen["headers"] = request.full_url, dict(request.header_items())
        return FakeUpstream()

    with patch.object(agent, "fetch_info", return_value=info), \
         patch.object(agent, "download_audio", side_effect=RuntimeError("ainda baixando")), \
         patch.object(agent, "urlopen", fake_urlopen):
        add(client)
        res = client.get(f"/tracks/{VIDEO}/audio?t=segredo&live=1", headers={"Range": "bytes=2-5"})
    assert res.status_code == 206
    assert res.data == b"2345"
    assert res.headers["Content-Range"] == "bytes 2-5/10"
    assert "Set-Cookie" not in res.headers
    assert seen["url"] == "https://rr1.googlevideo.example/audio"
    assert seen["headers"]["Range"] == "bytes=2-5" and seen["headers"]["User-agent"] == "yt"


def test_ouvir_baixando_sem_endereco_conhecido_da_404(client):
    with patch.object(agent, "fetch_info", return_value=INFO), patch.object(agent, "download_audio", fake_download):
        add(client)
    assert client.get(f"/tracks/{VIDEO}/audio?t=segredo&live=1").status_code == 404


def test_ouvir_baixando_com_youtube_fora_do_ar_da_502(client):
    info = {**INFO, "url": "https://rr1.googlevideo.example/audio"}
    with patch.object(agent, "fetch_info", return_value=info), patch.object(agent, "download_audio", fake_download), \
         patch.object(agent, "urlopen", side_effect=OSError("sem rede")):
        add(client)
        assert client.get(f"/tracks/{VIDEO}/audio?t=segredo&live=1").status_code == 502


def test_capa(client):
    with patch.object(agent, "fetch_info", return_value=INFO), patch.object(agent, "download_audio", fake_download):
        add(client)
    res = client.get(f"/tracks/{VIDEO}/thumb?t=segredo")
    assert res.status_code == 200 and res.mimetype == "image/jpeg"


def test_id_invalido_ou_inexistente_da_404(client):
    assert client.get("/tracks/..%2Fetc/audio?t=segredo").status_code == 404
    assert client.get(f"/tracks/{VIDEO}/audio?t=segredo").status_code == 404


def test_apaga_faixa(client):
    with patch.object(agent, "fetch_info", return_value=INFO), patch.object(agent, "download_audio", fake_download):
        add(client)
    assert client.delete(f"/tracks/{VIDEO}", headers=auth()).status_code == 204
    assert client.get("/tracks", headers=auth()).get_json() == []


def test_download_interrompido_vira_erro_ao_subir(tmp_path):
    library = agent.Library(tmp_path)
    library.write(VIDEO, {**INFO, "status": "downloading", "error": None, "added": 1})
    assert agent.Library(tmp_path).read(VIDEO)["status"] == "error"


def test_busca_devolve_resultados(client):
    flat = {"entries": [
        {"id": VIDEO, "title": "Achado", "channel": "Canal", "duration": 200},
        {"id": "channel-id-longo-demais", "title": "Canal, não vídeo"},
    ]}

    class FakeYdl:
        def __init__(self, options):
            assert options["extract_flat"] is True

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def extract_info(self, query, download):
            assert query == "ytsearch10:minha busca" and download is False
            return flat

    with patch.object(agent.yt_dlp, "YoutubeDL", FakeYdl):
        res = client.get("/search?q=minha busca", headers=auth())
    assert res.status_code == 200
    assert res.get_json() == [{
        "id": VIDEO, "title": "Achado", "channel": "Canal", "duration": 200,
        "thumbnail": f"https://i.ytimg.com/vi/{VIDEO}/mqdefault.jpg",
    }]


def test_busca_exige_token_e_texto(client):
    assert client.get("/search?q=x").status_code == 401
    assert client.get("/search?q=%20", headers=auth()).status_code == 400
    assert client.get(f"/search?q={'a' * 101}", headers=auth()).status_code == 400


def test_busca_que_falha_devolve_502(client):
    with patch.object(agent, "search_videos", side_effect=yt_dlp.utils.DownloadError("fora")):
        assert client.get("/search?q=x", headers=auth()).status_code == 502


@pytest.fixture
def two_users(tmp_path):
    """O app com a Isadora (administra, token "segredo") e a Maria (conta criada depois, com limite de 2)."""
    app = agent.create_app("segredo", ORIGIN, tmp_path, executor=SyncExecutor(), admin_name="isadora")
    maria_token = agent.Accounts(tmp_path / "users.json").create("maria", limit=2)
    return app.test_client(), maria_token


def as_maria(token):
    return {"Authorization": f"Bearer {token}"}


def add_video(client, headers, video=VIDEO):
    info = {**INFO, "id": video}
    with patch.object(agent, "fetch_info", return_value=info), patch.object(agent, "download_audio", fake_download):
        return client.post("/tracks", json={"url": f"https://youtu.be/{video}"}, headers=headers)


def test_cada_pessoa_ve_so_a_propria_biblioteca(two_users):
    client, maria = two_users
    add_video(client, auth())
    assert client.get("/tracks", headers=as_maria(maria)).get_json() == []
    assert len(client.get("/tracks", headers=auth()).get_json()) == 1


def test_nao_acessa_faixa_de_outra_pessoa(two_users):
    client, maria = two_users
    add_video(client, auth())
    for path in (f"/tracks/{VIDEO}/audio", f"/tracks/{VIDEO}/thumb", f"/tracks/{VIDEO}/audio?live=1"):
        assert client.get(path, headers=as_maria(maria)).status_code == 404
    assert client.delete(f"/tracks/{VIDEO}", headers=as_maria(maria)).status_code == 404
    assert client.get(f"/tracks/{VIDEO}/audio", headers=auth()).status_code == 200


def test_mesmo_video_e_baixado_uma_vez_so(two_users):
    client, maria = two_users
    add_video(client, auth())
    with patch.object(agent, "fetch_info", return_value=INFO), patch.object(agent, "download_audio") as again:
        res = client.post("/tracks", json={"url": URL}, headers=as_maria(maria))
    again.assert_not_called()
    assert res.status_code == 200 and res.get_json()["status"] == "ready"
    assert client.get(f"/tracks/{VIDEO}/audio", headers=as_maria(maria)).status_code == 200


def test_arquivo_so_some_quando_o_ultimo_dono_apaga(two_users, tmp_path):
    client, maria = two_users
    add_video(client, auth())
    client.post("/tracks", json={"url": URL}, headers=as_maria(maria))
    client.delete(f"/tracks/{VIDEO}", headers=auth())
    assert (tmp_path / "tracks" / VIDEO / agent.AUDIO_FILE).exists()
    assert client.get(f"/tracks/{VIDEO}/audio", headers=as_maria(maria)).status_code == 200
    client.delete(f"/tracks/{VIDEO}", headers=as_maria(maria))
    assert not (tmp_path / "tracks" / VIDEO).exists()


def test_limite_de_musicas_da_conta(two_users):
    client, maria = two_users
    assert add_video(client, as_maria(maria), "aaaaaaaaaaa").status_code == 202
    assert add_video(client, as_maria(maria), "bbbbbbbbbbb").status_code == 202
    res = add_video(client, as_maria(maria), "ccccccccccc")
    assert res.status_code == 403 and "Limite" in res.get_json()["error"]
    assert len(client.get("/tracks", headers=as_maria(maria)).get_json()) == 2
    assert add_video(client, auth(), "ccccccccccc").status_code == 202  # o limite é por pessoa


def test_me_mostra_conta_e_uso(two_users):
    client, maria = two_users
    add_video(client, as_maria(maria))
    assert client.get("/me", headers=as_maria(maria)).get_json() == {"name": "maria", "count": 1, "limit": 2}
    assert client.get("/me", headers=auth()).get_json()["name"] == "isadora"


def test_token_de_conta_apagada_para_de_valer(two_users, tmp_path):
    client, maria = two_users
    assert client.get("/tracks", headers=as_maria(maria)).status_code == 200
    agent.Accounts(tmp_path / "users.json").remove("maria")  # como o `manage.py remove-user`, com o servidor no ar
    assert client.get("/tracks", headers=as_maria(maria)).status_code == 401


def test_pastas_antigas_viram_da_conta_de_quem_administra(tmp_path):
    old = tmp_path / VIDEO
    old.mkdir()
    (old / agent.META_FILE).write_text(
        '{"id": "%s", "title": "Antiga", "channel": "C", "duration": 9, "status": "ready", "error": null, "added": 7}' % VIDEO,
        encoding="utf-8",
    )
    app = agent.create_app("segredo", ORIGIN, tmp_path, executor=SyncExecutor(), admin_name="isadora")
    assert (tmp_path / "tracks" / VIDEO / agent.META_FILE).exists() and not old.exists()
    [track] = app.test_client().get("/tracks", headers=auth()).get_json()
    assert track["title"] == "Antiga" and track["added"] == 7


def test_cors_so_pra_origem_permitida(client):
    ok = client.options("/tracks", headers={"Origin": ORIGIN})
    assert ok.status_code == 200
    assert ok.headers["Access-Control-Allow-Origin"] == ORIGIN
    assert "DELETE" in ok.headers["Access-Control-Allow-Methods"]
    outra = client.options("/tracks", headers={"Origin": "https://evil.example"})
    assert "Access-Control-Allow-Origin" not in outra.headers
