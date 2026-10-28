# Editor de edificios (prototipo)
Página web (`editor.html`, Three.js) + motor local (`engine.py`, Blender como motor con el add-on).
1. `pip install bpy` (o usar Blender: `blender -b -P engine.py`)
2. `python3 engine.py` y abrir http://127.0.0.1:8765
3. Dibujar la planta (se pega a la grilla), elegir pisos y «Crear malla real».
Hoy: planta libre + pisos + malla real. Falta: ventanas/puertas por clic, paredes curvas, techos, materiales, exportar.
