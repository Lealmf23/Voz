import numpy as np
import soundfile as sf

from app.engines.prosody.analyzer import analyze
from app.services import export as exp
from app.services import glossary as gl
from app.services import media, sync


def test_speech_regions(narration):
    y, sr = media.load_mono(narration)
    regions = media.detect_speech_regions(y, sr)
    assert len(regions) == 3
    assert abs(regions[0][0] - 1.0) < 0.1 and abs(regions[0][1] - 2.5) < 0.1


def test_glossary_protects_terms():
    terms = [gl.Term("Cities: Skylines II", do_not_translate=True), gl.Term("Steam Workshop", pt="Oficina Steam")]
    text, m = gl.protect("Mira Cities: Skylines II en Steam Workshop", terms, "pt-BR")
    assert "Cities" not in text
    assert gl.restore(text, m) == "Mira Cities: Skylines II en Oficina Steam"


def test_srt_format():
    assert exp.srt_time(3723.456) == "01:02:03,456"
    assert exp.build_srt([(0, 1.5, "Hola")]).startswith("1\n00:00:00,000 --> 00:00:01,500\nHola")


def test_fit_ratio_and_overlaps():
    assert sync.fit_ratio(3.0, 2.0) == 1.5
    assert sync.find_overlaps([(1, 0, 2), (2, 1.5, 3), (3, 4, 5)]) == [(1, 2)]


def test_timeline_starts_at_zero(tmp_path):
    clip = tmp_path / "c.wav"
    sf.write(str(clip), 0.5 * np.ones(4800), 48000)
    out = tmp_path / "o.wav"
    info = exp.render_timeline([(clip, 2.0)], 5.0, out)
    y, sr = sf.read(str(out))
    assert sf.info(str(out)).subtype == "PCM_24" and sr == 48000
    assert len(y) == 5 * 48000 and np.all(y[:2 * 48000] == 0) and y[2 * 48000 + 10] > 0.4
    assert info["duration"] == 5.0


def test_time_stretch_shortens(tmp_path):
    src = tmp_path / "a.wav"
    sf.write(str(src), 0.3 * np.sin(np.linspace(0, 2000, 48000 * 2)), 48000)
    res = sync.fit_segment(src, tmp_path / "b.wav", 1.7)
    assert 1.6 < res["duration"] < 1.8 and res["ratio"] > 1.0


def test_too_long_warns(tmp_path):
    src = tmp_path / "a.wav"
    sf.write(str(src), 0.3 * np.ones(48000 * 3), 48000)
    res = sync.fit_segment(src, tmp_path / "b.wav", 1.0)
    assert res["warnings"]


def test_prosody_f0_on_tone():
    sr = 16000
    t = np.arange(sr) / sr
    r = analyze(0.3 * np.sin(2 * np.pi * 150 * t), sr, "una dos tres")
    assert abs(r["f0_median_hz"] - 150) < 5 and r["words_per_second"] == 3.0


def test_import_rejects_bad_extension(tmp_path):
    import pytest
    with pytest.raises(media.MediaError):
        media.validate_extension(tmp_path / "x.txt", (".wav",))
