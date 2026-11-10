from lager.config import Settings, load_dotenv


def test_key_problems():
    assert Settings().key_problems() == "LAGER_API_KEY er ikke satt"
    assert Settings(api_key="a" * 40).key_problems() is None
    assert "for kort" in Settings(api_key="a" * 10).key_problems()
    assert "for kort" in Settings(api_key="a" * 40, read_key="b").key_problems()
    assert "lik" in Settings(api_key="a" * 40, read_key="a" * 40).key_problems()
    assert Settings(allow_insecure_no_auth=True).key_problems() is None


def test_dotenv_does_not_override_real_env(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text('# c\nA_TEST=from_file\nexport B_TEST="quoted value"\nBAD LINE\n', encoding="utf-8")
    monkeypatch.setenv("A_TEST", "from_env")
    monkeypatch.delenv("B_TEST", raising=False)
    load_dotenv(env)
    import os
    assert os.environ["A_TEST"] == "from_env"
    assert os.environ["B_TEST"] == "quoted value"
