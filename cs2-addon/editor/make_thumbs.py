"""Genera las miniaturas del catálogo (editor/thumbs/<estilo>.png) con el motor de render de Blender.
Uso:  LD_LIBRARY_PATH=/opt/egl EGL_PLATFORM=surfaceless LIBGL_ALWAYS_SOFTWARE=1 python3 make_thumbs.py   (solo las que faltan)"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import engine as e
a = e.a
import bpy
if not hasattr(bpy.types.Scene, "cs2_building"):
    a.register()
a.lib_ensure()
out = os.path.join(e.HERE, "thumbs")
n = 0
for name in a._LIB["names"]:
    st = a._LIB["styles"][name]
    path = os.path.join(out, e.slug(name) + ".png")
    if os.path.exists(path):
        continue
    try:
        a.render_thumb(st, path, size=128)
        n += 1
    except Exception as ex:
        print("falló", name, ex, flush=True)
print("miniaturas nuevas:", n, flush=True)
sys.stdout.flush()
os._exit(0)
