"""Pruebas de los componentes (botón + panel de ajustes aparte): python tests/test_componentes.py"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import bpy, bmesh
import cs2_edificio_addon as a

ok = True
def check(nombre, cond):
    global ok
    ok &= bool(cond); print(("OK    " if cond else "FALLA "), nombre)

a.register()
s = bpy.context.scene.cs2_building
s.custom_size = True; s.width, s.depth, s.floors = 24, 16, 4
bpy.ops.cs2.create_shape(kind="L"); bpy.ops.cs2.generate()
tris = lambda: [a.mesh_tris(o) for o in a._existing_lods(s)]
check("hay 8 componentes registrados", len(a._COMP["classes"]) == 8)

# --- todo el edificio
bpy.ops.cs2.comp_window('EXEC_DEFAULT', alcance="ALL", win_w=2.2, win_h=1.6, estilo="NONE")
check("ventana: ancho y alto en el edificio", abs(s.win_w - 2.2) < 1e-3 and abs(s.win_h - 1.6) < 1e-3)
bpy.ops.cs2.comp_door('EXEC_DEFAULT', alcance="ALL", door_w_g=1.8)
check("puerta: ancho general", abs(s.door_w_g - 1.8) < 1e-3)
bpy.ops.cs2.comp_balcony('EXEC_DEFAULT', alcance="ALL", balconies="all")
check("balcón: en todas las ventanas", s.balconies == "all")
bpy.ops.cs2.comp_cornice('EXEC_DEFAULT', string_courses=False)
check("cornisas: apagadas", not s.string_courses)
n0 = tris()
bpy.ops.cs2.comp_roof('EXEC_DEFAULT', alcance="ALL", r_preset_cargado="x", r_preset=list(a.ROOF_PRESETS)[1])
check("techo: modelo cambiado y edificio rehecho", s.r_preset == list(a.ROOF_PRESETS)[1] and tris() != n0)

# --- estilos dentro del panel
names = [n for n in a._LIB["names"] if a._LIB["styles"][n]["kind"] == "window"]
bpy.ops.cs2.comp_window('EXEC_DEFAULT', alcance="ALL", estilo=names[2], estilo_cargado="")
check("estilo elegido se usa", s.window_style == names[2])
bpy.ops.cs2.comp_window('EXEC_DEFAULT', alcance="ALL", estilo=names[2], estilo_cargado=names[2], e_cols=3, guardar_como="Prueba componentes")
check("estilo ajustado se guarda y se usa", s.window_style == "Prueba componentes" and a.get_style("Prueba componentes")["cols"] == 3)
a.delete_user_style("Prueba componentes")

# --- caras elegidas
bpy.ops.cs2.edit_shape()
obj = s.shape_object
bm = bmesh.from_edit_mesh(obj.data)
for f in bm.faces: f.select = False
W0 = [f for f in bm.faces if abs(f.normal.z) < .1][0].index
bm.faces.ensure_lookup_table(); bm.faces[W0].select = True; bmesh.update_edit_mesh(obj.data)
bpy.ops.cs2.comp_window('EXEC_DEFAULT', alcance="FACES", win_w=2.4, win_count=2, win_auto=False, estilo="__GLOBAL__")
bpy.ops.cs2.comp_door('EXEC_DEFAULT', alcance="FACES", door_w=1.6, door_mode="1", estilo="__GLOBAL__")
bpy.ops.cs2.comp_porch('EXEC_DEFAULT', alcance="FACES", modelo="FARMER")
bm = bmesh.from_edit_mesh(obj.data); bm.faces.ensure_lookup_table(); lays = a.wall_layers(bm, create=False)
f0 = bm.faces[W0]
check("cara: ventana guardada en la cara", abs(f0[lays["cs2_w_w"]] - 2.4) < 1e-3)
check("cara: puerta guardada en la cara", abs(f0[lays["cs2_d_w"]] - 1.6) < 1e-3)
check("cara: porche guardado en la cara", f0[lays["cs2_flags"]] & a.FLAG_POR)
print("TODO OK" if ok else "HAY FALLAS"); sys.exit(0 if ok else 1)
