"""Hojas de contacto con todas las ventanas, puertas y balcones incluidos (render Workbench, sin abrir Blender).

Uso:  python3 tools/hojas_componentes.py <carpeta_de_salida> <window|door|balcony> [por_hoja] [columnas]
Requiere bpy, numpy y Pillow. El render necesita una biblioteca EGL (ver «Herramientas de revisión» en CLAUDE.md):
    LD_LIBRARY_PATH=/opt/egl EGL_PLATFORM=surfaceless LIBGL_ALWAYS_SOFTWARE=1 python3 tools/hojas_componentes.py hojas window
"""
import math
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
out, kind = sys.argv[1:3]
per = int(sys.argv[3]) if len(sys.argv) > 3 else 20
cols = int(sys.argv[4]) if len(sys.argv) > 4 else 5

import bpy  # noqa: E402
import cs2_edificio_addon as a  # noqa: E402
from mathutils import Vector  # noqa: E402
from PIL import Image, ImageDraw  # noqa: E402

a.register()
for o in list(bpy.data.objects):
    bpy.data.objects.remove(o)
sc = bpy.context.scene
sc.render.engine = "BLENDER_WORKBENCH"
sc.display.shading.light = "STUDIO"
sc.display.shading.color_type = "MATERIAL"
T = 300
sc.render.resolution_x = sc.render.resolution_y = T
sc.world = bpy.data.worlds.new("w")
sc.world.color = (0.55, 0.65, 0.8)
cam = bpy.data.objects.new("cam", bpy.data.cameras.new("cam"))
sc.collection.objects.link(cam)
sc.camera = cam
cam.data.type = "ORTHO"
os.makedirs(out, exist_ok=True)
tiles = []
for i, (n, st) in enumerate([(n, st) for n, st in a.builtin_list() if st.get("kind") == kind]):
    ob, (L, H) = a.make_style_object(bpy.context, st, "t", sc.collection, (0, 0, 0))
    cam.data.ortho_scale = max(L, H) * 1.1
    ctr = Vector((0, 0, H / 2))
    az, e = math.radians(28), math.radians(14)
    cam.location = ctr + Vector((math.sin(az) * math.cos(e), -math.cos(az) * math.cos(e), math.sin(e))) * 20
    cam.rotation_euler = (ctr - cam.location).to_track_quat("-Z", "Y").to_euler()
    fp = os.path.join(out, "_t.png")
    sc.render.filepath = fp
    bpy.ops.render.render(write_still=True)
    im = Image.open(fp).convert("RGB")
    im.load()
    ImageDraw.Draw(im).text((4, 3), f"{i} {n}", fill=(255, 255, 255))
    tiles.append(im)
    me = ob.data
    bpy.data.objects.remove(ob)
    bpy.data.meshes.remove(me)
for s0 in range(0, len(tiles), per):
    chunk = tiles[s0:s0 + per]
    sheet = Image.new("RGB", (cols * T, -(-len(chunk) // cols) * T))
    for k, im in enumerate(chunk):
        sheet.paste(im, ((k % cols) * T, (k // cols) * T))
    sheet.save(os.path.join(out, f"{kind}_{s0 // per + 1}.png"))
os.remove(os.path.join(out, "_t.png"))
print("hojas listas:", len(tiles), "estilos")
sys.stdout.flush()
os._exit(0)   # Blender a veces se cuelga al cerrar
