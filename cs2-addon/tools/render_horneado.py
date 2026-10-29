"""Renderiza un edificio con las texturas HORNEADAS (UV únicas + sombras) y guarda también el mapa de sombras, para revisar manchas.

Uso:  python3 tools/render_horneado.py <carpeta_salida> [fuerza_sombra]
Mismo requisito de EGL que hojas_componentes.py.
"""
import math
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tests"))
out = sys.argv[1]
strength = float(sys.argv[2]) if len(sys.argv) > 2 else None
flat = "--liso" in sys.argv          # sin ladrillo: se ve solo la sombra
noshare = "--sin-compartir" in sys.argv
sys.argv = ["x"]
os.makedirs(out, exist_ok=True)

from common import a, bpy, make_brick, setup, lod_objs  # noqa: E402
from mathutils import Vector  # noqa: E402
import numpy as np  # noqa: E402

brick = make_brick(os.path.join(tempfile.gettempdir(), "cs2_render_brick.png"))
kw = dict(bake_ao=True, balconies="alternate",
          balcony_style="Balcón baranda maciza", string_courses=True)
if not flat:
    kw.update(ms_facade_mode="PBR", ms_facade_img_color=brick, ms_facade_tile=2.0)
if noshare:
    kw["bake_share"] = False
if os.environ.get("SHARE_AREA"):
    kw["bake_share_area"] = float(os.environ["SHARE_AREA"])
if os.environ.get("LOOSE"):
    kw["bake_share_loose"] = float(os.environ["LOOSE"])
if strength is not None:
    kw["bake_ao_str"] = strength
s = setup("BOX", "FLAT", 20, 12, 3, **kw)
objs = lod_objs(s)
p = a.props_to_params(s)
info = a.uv_generate(bpy.context, p, objs, None)
print("UVINFO", {k: info[0].get(k) for k in ("px_por_m", "islas", "compartidas", "uso")})
a.bake_ao_all(bpy.context, p, objs, None)
a.assign_textures(bpy.context, p, objs, None)
d = a.uv_state(objs[0].data)
ao = a.read_ao(p["building_name"], 0, d["uid"])
img = bpy.data.images.new("ao_dump", ao.shape[1], ao.shape[0])
px = np.ones((ao.shape[0], ao.shape[1], 4), np.float32)
px[..., 0] = px[..., 1] = px[..., 2] = ao
img.pixels.foreach_set(px.ravel())
img.filepath_raw = os.path.join(out, "ao.png")
img.file_format = "PNG"
img.save()
sc = bpy.context.scene
for o in bpy.data.objects:
    if o.name.endswith("_LOD1") or o.name.endswith("_LOD2"):
        o.hide_render = True
sc.render.engine = "BLENDER_WORKBENCH"
sc.display.shading.light = "FLAT"
sc.display.shading.color_type = "TEXTURE"
sc.render.resolution_x, sc.render.resolution_y = 1200, 800
cam = bpy.data.objects.new("cam", bpy.data.cameras.new("cam"))
sc.collection.objects.link(cam)
sc.camera = cam
cam.data.type = "ORTHO"
cam.data.ortho_scale = 16
ctr = Vector((0, 0, 4.5))
az, el = math.radians(-10), math.radians(8)
cam.location = ctr + Vector((math.sin(az) * math.cos(el), -math.cos(az) * math.cos(el), math.sin(el))) * 40
cam.rotation_euler = (ctr - cam.location).to_track_quat("-Z", "Y").to_euler()
sc.world = bpy.data.worlds.new("w")
sc.world.color = (0.6, 0.7, 0.85)
sc.render.filepath = os.path.join(out, "render.png")
bpy.ops.render.render(write_still=True)
sys.stdout.flush()
os._exit(0)
