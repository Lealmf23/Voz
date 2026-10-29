#!/bin/bash
# Abre el editor de edificios CS2. Usa Blender como motor:  BLENDER=/ruta/a/blender ./iniciar_editor.sh
# (alternativa sin Blender instalado: pip install bpy numpy pillow y  python3 engine.py --open)
cd "$(dirname "$0")"
exec "${BLENDER:-blender}" -b -P engine.py -- --port 8765 --open
