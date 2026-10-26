# Desarrollo

```
cd backend && pip install -r requirements/dev.txt && pytest      # 21 pruebas, motores simulados
cd apps/desktop && npm install && npm test && npm run build       # 7 pruebas + typecheck
```

- Añadir un motor: implementa la interfaz de `engines/base.py`, regístralo en `engines/setup.py`. La UI lo lista sola.
- Añadir un idioma: amplía `Lang`/`LANGS` (`routes.py`, `pipeline.py`, `api.ts`) y los códigos de cada motor.
- Las pruebas del backend usan audio sintético (tonos). **No validan la calidad de ningún modelo.**

## Pendiente
- Verificar Faster-Whisper, NLLB y Chatterbox en la PC con la RTX 5070 y ajustar los adaptadores.
- Tauri: compilar, lanzar el backend como sidecar, diálogos nativos de archivo, instalador NSIS.
- Gestor de modelos real (instalar/verificar hashes/eliminar), ajustes de hardware y directorios, WebSocket de progreso.
- Reordenar/mover segmentos en la línea de tiempo, detección de silencios editable, subida de muestras desde la UI.
- Cifrado de claves de proveedores externos (aún no hay ninguno).
