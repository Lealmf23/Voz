from types import SimpleNamespace

import pytest

from app.core.config import Settings
from app.engines.base import EngineUnavailable
from app.engines.translation.claude import ClaudeTranslation
from app.services import external
from tests.test_api import wait


class FakeClient:
    """Cliente SIMULADO de Anthropic: registra la petición y devuelve un texto fijo."""
    def __init__(self, text="Look at that traffic!", stop="end_turn"):
        self.calls, self.text, self.stop = [], text, stop
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kw):
        self.calls.append(kw)
        return SimpleNamespace(stop_reason=self.stop, content=[SimpleNamespace(type="text", text=self.text)])


def enabled_settings(tmp_path):
    s = Settings(data_dir=tmp_path / "d")
    s.ensure_dirs()
    external.update_claude(s, True, True, "sk-test", None)
    return s


def test_disabled_by_default(tmp_path):
    with pytest.raises(EngineUnavailable):
        ClaudeTranslation(Settings(data_dir=tmp_path))


def test_request_shape_and_context(tmp_path):
    fake = FakeClient()
    out = ClaudeTranslation(enabled_settings(tmp_path), client=fake).translate(
        "¡Miren ese tráfico!", "es", "en-US", ["Hola a todos"], ["Es una locura"])
    assert out == "Look at that traffic!"
    c = fake.calls[0]
    assert c["model"] == "claude-sonnet-5-5" and "temperature" not in c and "thinking" not in c
    body = c["messages"][0]["content"]
    assert "<segment>¡Miren ese tráfico!</segment>" in body and "Hola a todos" in body and "American English" in body
    assert "⟦G0⟧" in c["system"]


def test_refusal_and_empty_are_errors(tmp_path):
    s = enabled_settings(tmp_path)
    with pytest.raises(RuntimeError, match="rechaz"):
        ClaudeTranslation(s, client=FakeClient(stop="refusal")).translate("hola", "es", "en-US", [], [])
    with pytest.raises(RuntimeError):
        ClaudeTranslation(s, client=FakeClient(text="")).translate("hola", "es", "en-US", [], [])


def test_consent_required_and_key_not_in_plaintext(client, tmp_path):
    r = client.put("/api/external/claude", json={"enabled": True, "api_key": "sk-secret"})
    assert r.status_code == 400
    r = client.put("/api/external/claude", json={"enabled": True, "consent_confirmed": True, "api_key": "sk-secret"})
    assert r.status_code == 200 and r.json()["key_set"] and r.json()["consent_at"]
    assert "sk-secret" not in r.text
    for f in (tmp_path / "data").rglob("*"):
        if f.is_file() and f.suffix != ".db":
            assert "sk-secret" not in f.read_text(errors="ignore")
    client.delete("/api/external/claude/key")
    assert client.get("/api/external/claude").json()["key_set"] is False


def test_job_fails_clearly_when_not_enabled(client, narration):
    p = client.post("/api/projects", json={"name": "X"}).json()
    client.post(f"/api/projects/{p['id']}/import", json={"path": str(narration)})
    wait(client, client.post(f"/api/projects/{p['id']}/transcribe", json={"engine": "mock-silence"}).json())
    j = wait(client, client.post(f"/api/projects/{p['id']}/translate",
                                 json={"engine": "claude-translation", "language": "en-US"}).json())
    assert j["status"] == "error" and "desactivada" in j["error"]
