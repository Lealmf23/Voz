# Arquitectura

```
apps/desktop (React + TS + Tailwind, Tauri 2)  --HTTP/JSON-->  backend (FastAPI, 127.0.0.1:8765)
                                                                  ├─ api/routes.py        endpoints
                                                                  ├─ services/            media, glossary, sync, export, pipeline
                                                                  ├─ engines/             adaptadores intercambiables + registry
                                                                  ├─ workers/jobs.py      cola de 1 hilo (límite de VRAM) con cancelación
                                                                  └─ database/db.py       SQLite (metadatos); el audio va en disco
```

- **Motores:** `engines/base.py` define las interfaces (`TranscriptionEngine`, `TranslationEngine`, `TTSEngine`). Cada motor es un adaptador registrado en `engines/setup.py`. El registro mantiene un solo TTS cargado a la vez y los motores importan sus dependencias pesadas de forma perezosa.
- **Motores simulados** (`mock-*`): solo con `LEAL_ALLOW_MOCK=1` (activo por defecto en desarrollo). Se marcan como SIMULADO en la UI y en el informe de exportación. Un motor real no instalado produce un error visible; nunca se sustituye en silencio por uno simulado.
- **Tareas:** transcribir, traducir, sintetizar y analizar prosodia son trabajos en cola. La UI sondea `/api/jobs/{id}` (WebSocket pendiente).
- **Sincronización:** cada segmento tiene un hueco `[start, end]`. Si el audio generado es más largo, se acelera con `atempo` hasta x1,25; si aun así no cabe, se avisa. Los WAV exportados parten de t=0 con la duración del original.
- **Seguridad:** escucha solo en localhost, valida el Host, CORS limitado a la app, sesiones de BD por petición. Los perfiles de voz exigen confirmar la autorización del titular.
- **Prosodia:** `engines/prosody/analyzer.py` mide RMS, F0 (autocorrelación), pausas y palabras por segundo. No estima emociones. Ningún motor integrado transfiere prosodia frase a frase (ver AI_MODELS.md).
