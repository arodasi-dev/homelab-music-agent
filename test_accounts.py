import pytest

import manage
from accounts import Accounts, AccountError, LimitReached, hash_token


@pytest.fixture
def accounts(tmp_path):
    return Accounts(tmp_path / "users.json")


def test_bootstrap_cria_a_conta_de_quem_administra_com_as_faixas_antigas(accounts):
    accounts.bootstrap("isadora", "token-antigo", {"aaaaaaaaaaa": 5.0})
    assert accounts.authenticate("token-antigo") == "isadora"
    assert accounts.tracks("isadora") == {"aaaaaaaaaaa": 5.0}


def test_bootstrap_so_roda_uma_vez(accounts):
    accounts.bootstrap("isadora", "um", {})
    accounts.bootstrap("outra", "dois", {})
    assert accounts.authenticate("dois") is None
    assert [u["name"] for u in accounts.summary()] == ["isadora"]


def test_token_nao_fica_guardado_em_texto_puro(accounts):
    accounts.bootstrap("isadora", "segredo", {})
    assert "segredo" not in accounts.path.read_text(encoding="utf-8")
    assert hash_token("segredo") in accounts.path.read_text(encoding="utf-8")


def test_cria_conta_e_autentica(accounts):
    accounts.bootstrap("isadora", "x", {})
    token = accounts.create("maria", limit=3)
    assert accounts.authenticate(token) == "maria"
    assert accounts.authenticate("errado") is None
    assert accounts.authenticate("") is None


@pytest.mark.parametrize("name", ["", "a", "Maria", "com espaco", "../x", "x" * 33])
def test_nome_invalido(accounts, name):
    accounts.bootstrap("isadora", "x", {})
    with pytest.raises(AccountError):
        accounts.create(name)


def test_nome_repetido(accounts):
    accounts.bootstrap("isadora", "x", {})
    with pytest.raises(AccountError):
        accounts.create("isadora")


def test_limite_de_musicas(accounts):
    accounts.bootstrap("isadora", "x", {})
    accounts.create("maria", limit=2)
    accounts.add_track("maria", "aaaaaaaaaaa")
    accounts.add_track("maria", "aaaaaaaaaaa")  # repetida não conta de novo
    accounts.add_track("maria", "bbbbbbbbbbb")
    with pytest.raises(LimitReached):
        accounts.add_track("maria", "ccccccccccc")


def test_remover_faixa_diz_se_virou_orfa(accounts):
    accounts.bootstrap("isadora", "x", {"aaaaaaaaaaa": 1.0})
    accounts.create("maria")
    accounts.add_track("maria", "aaaaaaaaaaa")
    assert accounts.remove_track("isadora", "aaaaaaaaaaa") is False  # a Maria ainda tem
    assert accounts.remove_track("maria", "aaaaaaaaaaa") is True


def test_token_novo_invalida_o_antigo(accounts):
    accounts.bootstrap("isadora", "x", {})
    antigo = accounts.create("maria")
    novo = accounts.reset_token("maria")
    assert accounts.authenticate(antigo) is None
    assert accounts.authenticate(novo) == "maria"


def test_remover_conta_devolve_as_faixas(accounts):
    accounts.bootstrap("isadora", "x", {})
    accounts.create("maria")
    accounts.add_track("maria", "aaaaaaaaaaa")
    assert accounts.remove("maria") == ["aaaaaaaaaaa"]
    assert accounts.is_orphan("aaaaaaaaaaa")
    with pytest.raises(AccountError):
        accounts.info("maria")


def run_cli(tmp_path, monkeypatch, *argv):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    manage.main(list(argv))


def test_cli_cria_lista_e_remove(tmp_path, monkeypatch, capsys):
    Accounts(tmp_path / "users.json").bootstrap("isadora", "x", {})
    run_cli(tmp_path, monkeypatch, "add-user", "maria", "--limit", "5")
    token = capsys.readouterr().out.split(": ")[-1].strip()
    assert Accounts(tmp_path / "users.json").authenticate(token) == "maria"
    run_cli(tmp_path, monkeypatch, "list-users")
    assert "maria" in capsys.readouterr().out
    run_cli(tmp_path, monkeypatch, "set-limit", "maria", "9")
    assert Accounts(tmp_path / "users.json").info("maria")["limit"] == 9
    run_cli(tmp_path, monkeypatch, "remove-user", "maria")
    assert [u["name"] for u in Accounts(tmp_path / "users.json").summary()] == ["isadora"]


def test_cli_remove_so_apaga_do_disco_o_que_ninguem_mais_tem(tmp_path, monkeypatch):
    accounts = Accounts(tmp_path / "users.json")
    accounts.bootstrap("isadora", "x", {"aaaaaaaaaaa": 1.0})
    accounts.create("maria")
    for track_id in ("aaaaaaaaaaa", "bbbbbbbbbbb"):
        accounts.add_track("maria", track_id)
        (tmp_path / "tracks" / track_id).mkdir(parents=True)
    run_cli(tmp_path, monkeypatch, "remove-user", "maria")
    assert (tmp_path / "tracks" / "aaaaaaaaaaa").exists()  # a Isadora ainda tem
    assert not (tmp_path / "tracks" / "bbbbbbbbbbb").exists()


def test_cli_sem_contas_avisa(tmp_path, monkeypatch):
    with pytest.raises(SystemExit):
        run_cli(tmp_path, monkeypatch, "list-users")


def test_cli_conta_inexistente_avisa(tmp_path, monkeypatch):
    Accounts(tmp_path / "users.json").bootstrap("isadora", "x", {})
    with pytest.raises(SystemExit):
        run_cli(tmp_path, monkeypatch, "set-limit", "ninguem", "3")
