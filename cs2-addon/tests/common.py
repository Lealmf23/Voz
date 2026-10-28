"""Utilidades compartidas por las pruebas (se ejecutan con el módulo bpy: `pip install bpy`, sin abrir Blender)."""
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import bpy  # noqa: E402
import cs2_edificio_addon as a  # noqa: E402

STYLES = dict(balconies="alternate", balcony_style="Balcón vidrio y pasamanos",
              window_style="Ventana 2x2 cuarterones", door_style="Garaje seccional doble")


def setup(kind="L", preset="FLAT", w=24, d=16, floors=5, styles=True, **kw):
    """Crea la forma y genera el edificio con los ajustes dados. Devuelve las propiedades de la escena."""
    if not hasattr(bpy.types.Scene, "cs2_building"):
        a.register()
    s = bpy.context.scene.cs2_building
    s.custom_size = True
    s.width, s.depth, s.floors = w, d, floors
    bpy.ops.cs2.create_shape(kind=kind)
    s.r_preset = preset
    opts = dict(STYLES) if styles else {}
    opts.update(kw)
    for k, v in opts.items():
        setattr(s, k, v)
    bpy.ops.cs2.generate()
    if not (kw.get('_main_only')):
        bpy.ops.cs2.regen_lods()
    return s


def make_brick(path):
    """Textura de ladrillo que se repite (8 por 2 m, 26 hiladas): sirve para probar la alineación del patrón."""
    import numpy as np
    from PIL import Image
    n, rows = 256, 26
    img = np.zeros((n, n, 3), np.float32)
    for y in range(n):
        for x in range(n):
            row = int(y * rows / n)
            mx = ((x / n * 8) + (0.5 if row % 2 else 0)) % 1.0
            ry = (y * rows / n) % 1.0
            img[y, x] = (0.8, 0.78, 0.72) if (ry < 0.13 or mx < 0.04) else (0.62 + 0.04 * ((row * 7 + int(x / n * 8)) % 3), 0.28, 0.2)
    Image.fromarray((img * 255).astype(np.uint8)).save(path)
    return path


def lod_objs(s):
    return a._lod_objs(s)


def lod_tris(s):
    return [a.mesh_tris(o) for o in lod_objs(s)]


def finite_mesh(me):
    import math
    return all(math.isfinite(c) for v in me.vertices for c in v.co)


def baselines():
    p = os.path.join(HERE, "baselines.json")
    return json.load(open(p)) if os.path.exists(p) else {}


def result(**metrics):
    """Imprime las métricas para el corredor (una línea JSON)."""
    print("RESULT " + json.dumps(metrics, ensure_ascii=False))
