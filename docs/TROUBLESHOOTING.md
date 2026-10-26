# Problemas habituales

- **`CUDA no disponible` / `no kernel image` en la RTX 5070:** el wheel de PyTorch no incluye sm_120. Instala el wheel cu128 (torch ≥ 2.7) y comprueba con `scripts/validate_hardware.py`. Instalar `chatterbox-tts` puede reemplazar tu torch.
- **Triton `sm_120 is not defined`:** afecta a herramientas basadas en Triton en Windows aunque PyTorch funcione (informado en el foro de PyTorch). No lo usamos directamente.
- **`FFmpeg no encontrado`:** instálalo y reinicia la terminal para que se actualice el PATH.
- **Una tarea termina en error `... no está instalado`:** el motor real elegido en Modelos de IA no está instalado. Instala `requirements/ai.txt` o elige un motor simulado (solo para probar).
- **Audio generado demasiado largo:** la advertencia del segmento indica que la traducción no cabe en el hueco; acórtala en lugar de acelerar más.
- **Sin memoria de VRAM:** la cola ejecuta una tarea a la vez y descarga el TTS anterior al cargar otro. Cierra otras apps de GPU.
