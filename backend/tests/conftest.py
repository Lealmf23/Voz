import numpy as np
import pytest
import soundfile as sf
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app


@pytest.fixture
def client(tmp_path):
    app = create_app(Settings(data_dir=tmp_path / "data", allow_mock_engines=True))
    return TestClient(app)


@pytest.fixture
def narration(tmp_path):
    """Audio sintético: 3 ráfagas de tono separadas por silencios (NO es voz real)."""
    sr = 48000
    t = np.arange(int(1.5 * sr)) / sr
    tone = 0.3 * np.sin(2 * np.pi * 200 * t)
    gap = np.zeros(int(0.8 * sr))
    y = np.concatenate([np.zeros(sr), tone, gap, tone, gap, tone, np.zeros(sr)])
    p = tmp_path / "narracion con espacios.wav"
    sf.write(str(p), y, sr)
    return p


@pytest.fixture(autouse=True)
def memory_keyring(monkeypatch):
    """Almacén de claves en memoria: los tests nunca tocan el del sistema ni usan claves reales."""
    import keyring
    from keyring.backend import KeyringBackend

    class Mem(KeyringBackend):
        priority = 1
        def __init__(self): self.d = {}
        def get_password(self, s, u): return self.d.get((s, u))
        def set_password(self, s, u, p): self.d[(s, u)] = p
        def delete_password(self, s, u): self.d.pop((s, u), None)

    keyring.set_keyring(Mem())
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
