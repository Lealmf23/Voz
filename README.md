# Leal Dub Studio

Aplicación de escritorio (Windows 11) para doblar narraciones del español a en-US y pt-BR conservando la voz del narrador. Todo en local.

## Estado real (honesto)

| Parte | Estado |
|---|---|
| Backend FastAPI (proyectos, segmentos, voces, glosario, tareas, exportación WAV 24 bit/SRT) | Implementado. 15 pruebas pasan con **motores simulados** |
| Importación, silencios, niveles, time-stretch, línea de tiempo desde t=0 | Implementado y probado con audio sintético |
| Análisis de prosodia (RMS, F0, pausas, ritmo) | Implementado. No infiere emociones |
| Faster-Whisper, Chatterbox | Adaptadores escritos, **nunca ejecutados** (sin GPU en el entorno de desarrollo) |
| Traducción local real | **Pendiente.** Solo existe un traductor simulado |
| Interfaz React/Tauri | **Pendiente** |
| Gestor de modelos, instalador, A/B | **Pendiente** |

Los motores `mock-*` no transcriben, no traducen ni clonan: sirven para probar la tubería y se marcan como SIMULADO en el informe de exportación.

## Probar el backend

```
cd backend
python -m venv .venv && .venv\Scripts\activate
pip install -r requirements/dev.txt
pytest
python -m app.main        # http://127.0.0.1:8765/docs
```

## Validar tu PC (RTX 5070)

```
python scripts/validate_hardware.py
python scripts/voice_clone_test.py mi_voz.wav salida
```

Investigación de motores: [docs/PHASE0_RESEARCH.md](docs/PHASE0_RESEARCH.md). Los datos se guardan en `~/LealDubStudio` (variable `LEAL_DATA_DIR`). El backend solo escucha en 127.0.0.1.
