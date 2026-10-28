"""Renderiza un edificio de prueba con texturas (vista rápida) en Workbench, para comprobar a ojo el giro de las texturas.

Uso:  python3 tools/render_edificio.py <salida.png> [giro_del_material_en_grados] [giro_de_la_pared_frontal_en_cuartos]
Mismo requisito de EGL que hojas_componentes.py.
"""
import math
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tests"))
out = sys.argv[1]
rot = float(sys.argv[2]) if len(sys.argv) > 2 else 0.0
frot = int(sys.argv[3]) if len(sys.argv) > 3 else 0
sys.argv = ["x"]

from common import a, bpy, make_brick, setup  # noqa: E402
import bmesh  # noqa: E402
from mathutils import Vector  # noqa: E402

brick = make_brick(os.path.join(tempfile.gettempdir(), "cs2_render_brick.png"))
s = setup("BOX", "FLAT", 14, 10, 2, styles=True, ms_facade_mode="PBR", ms_facade_img_color=brick, ms_facade_tile=2.0)
s.ms_facade_rot = rot
if frot:
    obj = s.shape_object
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    bm.transform(obj.matrix_world)
    lays = a.wall_layers(bm, create=True)
    bm.transform(obj.matrix_world.inverted())
    for f in bm.faces:
        if f.normal.y < -0.9:
            f[lays["cs2_f_rot"]] = frot
    bm.to_mesh(obj.data)
    bm.free()
bpy.ops.cs2.generate()
bpy.ops.cs2.regen_lods()
s.pbr_preview = True
a.quick_view_apply(s)
sc = bpy.context.scene
for o in bpy.data.objects:
    if o.name.endswith("_LOD1") or o.name.endswith("_LOD2"):
        o.hide_render = True
for m in bpy.data.materials:
    if m.node_tree and m.get("cs2_prev_on"):
        nd = m.node_tree.nodes.get("cs2_prev_base")
        if nd:
            m.node_tree.nodes.active = nd
sc.render.engine = "BLENDER_WORKBENCH"
sc.display.shading.light = "FLAT"
sc.display.shading.color_type = "TEXTURE"
sc.render.resolution_x, sc.render.resolution_y = 640, 480
cam = bpy.data.objects.new("cam", bpy.data.cameras.new("cam"))
sc.collection.objects.link(cam)
sc.camera = cam
cam.data.type = "ORTHO"
cam.data.ortho_scale = 20
ctr = Vector((0, 0, 3))
az, el = math.radians(-30), math.radians(25)
cam.location = ctr + Vector((math.sin(az) * math.cos(el), -math.cos(az) * math.cos(el), math.sin(el))) * 40
cam.rotation_euler = (ctr - cam.location).to_track_quat("-Z", "Y").to_euler()
sc.world = bpy.data.worlds.new("w")
sc.world.color = (0.6, 0.7, 0.85)
sc.render.filepath = out
bpy.ops.render.render(write_still=True)
sys.stdout.flush()
os._exit(0)
