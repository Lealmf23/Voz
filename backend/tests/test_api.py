import time


def wait(client, job, timeout=30):
    t = time.time()
    while time.time() - t < timeout:
        j = client.get(f"/api/jobs/{job['id']}").json()
        if j["status"] in ("completed", "error", "cancelled"):
            return j
        time.sleep(0.05)
    raise TimeoutError


def test_localhost_only(client):
    assert client.get("/api/health", headers={"host": "evil.example.com"}).status_code == 400


def test_full_flow_with_mocks(client, narration, tmp_path):
    """Flujo completo con motores SIMULADOS: valida la tubería, no la calidad de IA."""
    p = client.post("/api/projects", json={"name": "Demo"}).json()
    r = client.post(f"/api/projects/{p['id']}/import", json={"path": str(narration)})
    assert r.status_code == 200 and r.json()["audio_info"]["speech_regions"] == 3

    j = wait(client, client.post(f"/api/projects/{p['id']}/transcribe", json={"engine": "mock-silence"}).json())
    assert j["status"] == "completed" and j["result"]["mock"] is True
    segs = client.get(f"/api/projects/{p['id']}").json()["segments"]
    assert len(segs) == 3 and all(s["confidence"] is None and s["original_text"] == "" for s in segs)

    for i, s in enumerate(segs):
        client.patch(f"/api/segments/{s['id']}", json={"original_text": f"Miren el tráfico número {i}"})
    client.post("/api/glossary", json={"term": "tráfico", "en": "traffic", "pt": "trânsito"})

    v = client.post("/api/voices", json={"name": "Yo", "sample_paths": [str(narration)], "consent_confirmed": True})
    assert v.status_code == 201
    client.patch(f"/api/projects/{p['id']}", json={"voice_profile_id": v.json()["id"]})

    for lang in ("en-US", "pt-BR"):
        assert wait(client, client.post(f"/api/projects/{p['id']}/translate",
                                        json={"engine": "mock-translate", "language": lang}).json())["status"] == "completed"
        assert wait(client, client.post(f"/api/projects/{p['id']}/synthesize",
                                        json={"engine": "mock-tts", "language": lang}).json())["status"] == "completed"

    segs = client.get(f"/api/projects/{p['id']}").json()["segments"]
    assert "traffic" in segs[0]["translations"]["en-US"] and "trânsito" in segs[0]["translations"]["pt-BR"]
    assert client.get(f"/api/segments/{segs[0]['id']}/audio/en-US").status_code == 200

    # regenerar una sola frase
    assert client.post(f"/api/segments/{segs[1]['id']}/synthesize",
                       json={"language": "en-US", "engine": "mock-tts"}).status_code == 200

    out = tmp_path / "exports con espacios"
    rep = client.post(f"/api/projects/{p['id']}/export", json={"output_dir": str(out)}).json()
    assert (out / "english" / "Leal_EN.wav").exists() and (out / "portuguese" / "Leal_PT_BR.srt").exists()
    assert (out / "reports" / "export_report.json").exists()
    assert abs(rep["languages"]["en-US"]["duration"] - 8.1) < 0.2
    assert any("SIMULADO" in w for w in rep["warnings"])


def test_voice_requires_consent(client, narration):
    r = client.post("/api/voices", json={"name": "X", "sample_paths": [str(narration)], "consent_confirmed": False})
    assert r.status_code == 400


def test_import_invalid_file(client, tmp_path):
    p = client.post("/api/projects", json={"name": "X"}).json()
    bad = tmp_path / "x.wav"
    bad.write_text("no es audio")
    assert client.post(f"/api/projects/{p['id']}/import", json={"path": str(bad)}).status_code == 400


def test_segment_validation(client, narration):
    p = client.post("/api/projects", json={"name": "X"}).json()
    client.post(f"/api/projects/{p['id']}/import", json={"path": str(narration)})
    wait(client, client.post(f"/api/projects/{p['id']}/transcribe", json={"engine": "mock-silence"}).json())
    s = client.get(f"/api/projects/{p['id']}").json()["segments"][0]
    assert client.patch(f"/api/segments/{s['id']}", json={"start": 5, "end": 4}).status_code == 422


def test_real_engine_unavailable_is_reported(client, narration):
    p = client.post("/api/projects", json={"name": "X"}).json()
    client.post(f"/api/projects/{p['id']}/import", json={"path": str(narration)})
    j = wait(client, client.post(f"/api/projects/{p['id']}/transcribe", json={"engine": "faster-whisper"}).json())
    assert j["status"] == "error" and "faster-whisper" in j["error"]
