# Instalación en Windows 11

> Estos pasos **no se han ejecutado en Windows**; se escribieron a partir de la documentación oficial de cada proyecto. Si algo falla, anótalo en TROUBLESHOOTING.md.

## 1. Requisitos
- Python 3.11 (Chatterbox se prueba con 3.11). `py -3.11 --version`
- Node.js 20+ y Rust (https://rustup.rs) para la app de escritorio.
- FFmpeg y FFprobe en el PATH (`winget install Gyan.FFmpeg`).
- Driver NVIDIA reciente. Verifica con `nvidia-smi`.

## 2. Backend (sin IA, para probar)
```
cd backend
py -3.11 -m venv .venv
.venv\Scripts\activate
pip install -r requirements\dev.txt
pytest
python -m app.main
```

## 3. PyTorch para la RTX 5070 (Blackwell, sm_120)
Instala PyTorch **antes** que los modelos, con un wheel CUDA 12.8 (cu128), versión 2.7 o superior. El comando exacto está en https://pytorch.org/get-started/locally/ (elige Pip, Windows, CUDA 12.8). Después:
```
python scripts\validate_hardware.py
```
Debe mostrar `sm_120: OK`. Si dice FALTA o CUDA no disponible, tienes un wheel antiguo o CPU.

## 4. Modelos (descargas de varios GB, solo cuando quieras)
```
pip install -r backend\requirements\ai.txt
```
`chatterbox-tts` puede fijar otra versión de torch y sustituir tu wheel cu128. Si pasa, vuelve a instalar el wheel cu128 y repite `validate_hardware.py`. Los pesos se descargan al primer uso.

Prueba de viabilidad de la voz (la prueba más importante):
```
python scripts\voice_clone_test.py mi_voz.wav salida
```

### Opcional: traducción con Claude (en línea)
```
pip install -r backend\requirements\cloud.txt
```
Luego, en la app: Configuración → Servicios externos → acepta el consentimiento y pega tu clave API. En Modelos de IA elige «Claude (API en línea)» como motor de traducción.

## 5. Interfaz
```
cd apps\desktop
npm install
npm run dev        # navegador en http://localhost:1420 (el backend debe estar en marcha)
npm run tauri dev  # app de escritorio (sin probar)
```
Los datos se guardan en `%USERPROFILE%\LealDubStudio` (cámbialo con `LEAL_DATA_DIR`).
