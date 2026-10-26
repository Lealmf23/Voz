# Leal Dub Studio

Aplicación de escritorio (Windows 11) para doblar narraciones del español a en-US y pt-BR conservando la voz del narrador. Todo en local.

## Estado real (honesto)

| Parte | Estado |
|---|---|
| Backend FastAPI (proyectos, segmentos, voces, glosario, tareas, exportación WAV 24 bit/SRT) | Implementado. 21 pruebas pasan con **motores simulados** |
| Importación, silencios, niveles, time-stretch, línea de tiempo desde t=0 | Implementado y probado con audio sintético |
| Análisis de prosodia (RMS, F0, pausas, ritmo) | Implementado. No infiere emociones |
| Interfaz React: Dashboard, Proyectos, Estudio (tabla de segmentos, forma de onda, regenerar, A/B manual, exportar), Voces, Glosario, Modelos, Configuración | Implementada. 7 pruebas de componentes; flujo recorrido en un navegador real con motores simulados |
| Traducción con Claude por API (opcional, en línea, desactivada por defecto; consentimiento + clave en el almacén del sistema) | Implementada; probada **solo con un cliente simulado**, no con la API real |
| Faster-Whisper, NLLB, Chatterbox | Adaptadores escritos, **nunca ejecutados** (sin GPU en el entorno de desarrollo) |
| Tauri 2 (esqueleto), instalador | Archivos escritos, **sin compilar**. Sin sidecar ni diálogos nativos |
| Gestor de modelos (instalar/verificar/eliminar), ajustes de hardware y directorios, WebSocket | **Pendiente** (la UI lo indica) |
| Línea de tiempo con arrastre de segmentos | **Pendiente**: hoy se editan tiempos vía API; la UI muestra la forma de onda y la tabla |

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

Interfaz: `cd apps/desktop && npm install && npm run dev` (con el backend en marcha). Más detalle en `docs/`.
