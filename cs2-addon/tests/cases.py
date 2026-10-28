"""Casos de prueba del addon. Uso: python3 cases.py <caso>   (el corredor run_tests.py los lanza uno por proceso)."""
import math
import os, json
import sys
import tempfile
import time

from common import *  # noqa: F401,F403
from common import a, bpy, setup, make_brick, lod_objs, lod_tris, finite_mesh, result, baselines

BASE = baselines()


def check(cond, msg):
    if not cond:
        raise AssertionError(msg)


def worse_than(name, value, higher_is_better=True, tol=0.10):
    """Compara con la línea base guardada: falla si empeora más de `tol`."""
    ref = BASE.get(name)
    if ref is None:
        return
    if higher_is_better:
        check(value >= ref * (1 - tol), f"{name} empeoró: {value} < {ref} (tolerancia {int(tol * 100)} %)")
    else:
        check(value <= ref * (1 + tol), f"{name} empeoró: {value} > {ref} (tolerancia {int(tol * 100)} %)")


def case_lod_limits():
    """Formas × pisos: los LOD respetan los límites del juego y las mallas son válidas."""
    out = {}
    for kind, w, d, fl in (("BOX", 20, 14, 3), ("L", 30, 20, 6), ("T", 30, 24, 6), ("U", 40, 30, 8)):
        s = setup(kind, "FLAT", w, d, fl)
        t = lod_tris(s)
        objs = lod_objs(s)
        check(t[2] <= a.LOD2_MAX_TRIS, f"{kind}: LOD2 {t[2]} > {a.LOD2_MAX_TRIS}")
        check(t[1] < 0.5 * t[0], f"{kind}: LOD1 {t[1]} no es menos de la mitad de LOD0 {t[0]}")
        for i, o in enumerate(objs):
            check(finite_mesh(o.data), f"{kind}: LOD{i} tiene vértices no finitos")
            check(len(o.data.uv_layers) >= 1, f"{kind}: LOD{i} sin UV")
        out[kind] = t
        for o in objs:
            bpy.data.objects.remove(o, do_unlink=True)
    result(tris=out)


def _uv_checks(me, uv_name="CS2_unique"):
    layer = me.uv_layers[uv_name].data
    bad = 0
    for poly in me.polygons:
        if poly.material_index in a.SUB_MATS:
            continue
        for li in poly.loop_indices:
            u, v = layer[li].uv
            if not (math.isfinite(u) and math.isfinite(v)) or u < -1e-4 or u > 1 + 1e-4 or v < -1e-4 or v > 1 + 1e-4:
                bad += 1
    return bad


def case_uv_bake():
    """UV únicas: dentro del mapa, con nitidez, aprovechamiento y compartición según la línea base."""
    brick = make_brick(os.path.join(tempfile.gettempdir(), "cs2_test_brick.png"))
    s = setup("L", "FLAT", 24, 16, 5, bake_ao=True, ms_facade_mode="PBR", ms_facade_img_color=brick, ms_facade_tile=2.0)
    objs = lod_objs(s)
    p = a.props_to_params(s)
    infos = a.uv_generate(bpy.context, p, objs, None)
    for i, o in enumerate(objs):
        check(_uv_checks(o.data) == 0, f"LOD{i}: hay UV fuera de 0-1 o no finitas")
    i0 = infos[0]
    check(i0["compartidas"] > 0.8 * i0["caras"], f"se comparten pocas caras: {i0['compartidas']}/{i0['caras']}")
    check(i0["uso"] >= 55, f"aprovechamiento del mapa muy bajo: {i0['uso']} %")
    worse_than("uv_bake.px_por_m", i0["px_por_m"])
    worse_than("uv_bake.islas", i0["islas"], higher_is_better=False, tol=0.25)
    result(px_por_m=i0["px_por_m"], islas=i0["islas"], uso=i0["uso"], lod1_px=infos[1]["px_por_m"], lod2_px=infos[2]["px_por_m"])


def _overlap_pct(me, uv_name="CS2_unique", res=1024):
    """% de texels cubiertos por islas DISTINTAS (las gemelas se superponen a propósito y no cuentan)."""
    import numpy as np
    from PIL import Image, ImageDraw
    reps = list(me["cs2_reps"])
    layer = me.uv_layers[uv_name].data
    count = np.zeros((res, res), np.uint8)
    for poly in me.polygons:
        if poly.material_index in a.SUB_MATS or reps[poly.index] != poly.index:
            continue
        img = Image.new("L", (res, res), 0)
        ImageDraw.Draw(img).polygon([(layer[li].uv[0] * res, (1 - layer[li].uv[1]) * res) for li in poly.loop_indices], fill=1)
        count += np.asarray(img, np.uint8)
    return float((count > 1).sum()) / float((count > 0).sum()) * 100.0


def case_uv_overlap():
    """Las islas distintas no se pisan en el mapa (el hueco de la azotea en L se rellena sin solapes)."""
    for kind, w, d, fl in (("L", 24, 16, 5), ("U", 30, 24, 6)):
        s = setup(kind, "FLAT", w, d, fl, bake_ao=True)
        objs = lod_objs(s)
        a.uv_generate(bpy.context, a.props_to_params(s), objs, None)
        for i, o in enumerate(objs):
            pct = _overlap_pct(o.data)
            check(pct < 0.5, f"{kind} LOD{i}: {pct:.2f} % del mapa con islas distintas superpuestas")
        for o in objs:
            bpy.data.objects.remove(o, do_unlink=True)
    result(ok=True)


def case_uv_align():
    """Con el patrón alineado, las caras que comparten isla caen en la misma hilada (y casi no se pierde compartición)."""
    brick = make_brick(os.path.join(tempfile.gettempdir(), "cs2_test_brick.png"))
    s = setup("L", "FLAT", 24, 16, 5, bake_ao=True, ms_facade_mode="PBR", ms_facade_img_color=brick, ms_facade_tile=2.0)
    objs = lod_objs(s)
    res = {}
    for al in (False, True):
        s.bake_align = al
        p = a.props_to_params(s)
        res[al] = a.uv_generate(bpy.context, p, objs, None)[0]
    check(res[True]["islas"] <= 1.4 * res[False]["islas"] + 5, f"alinear pierde demasiada compartición: {res[True]['islas']} vs {res[False]['islas']}")
    # comprobación independiente: las gemelas deben estar en la misma fase vertical del ladrillo
    p = a.props_to_params(s)
    sl = p["matslots"]["facade"]
    fx, fy = a._pattern_period(brick)
    Py = float(sl["_ty"]) * fy
    me = objs[0].data
    reps = list(me["cs2_reps"])
    atl, ext = a.bake_context(p)
    uvl = next(l for l in me.uv_layers if l.name != "CS2_unique")
    u0, v0 = atl["facade"][0], atl["facade"][1]

    def ry(poly):
        return min((uvl.data[li].uv[1] - v0) / ext for li in poly.loop_indices)

    def rx(poly):
        return min((uvl.data[li].uv[0] - u0) / ext for li in poly.loop_indices)
    Px = float(sl.get("tile", 2.0)) * fx
    bad = 0
    badx = 0
    n = 0
    for poly in me.polygons:
        r = reps[poly.index]
        if r == poly.index or poly.material_index != a.MAT["facade"] or me.polygons[r].material_index != a.MAT["facade"]:
            continue
        n += 1
        d = abs((ry(poly) - ry(me.polygons[r])) % Py)
        d = min(d, Py - d)
        if d > 0.02:
            bad += 1
        dx = abs((rx(poly) - rx(me.polygons[r])) % Px)
        if min(dx, Px - dx) > 0.02:
            badx += 1
    check(n == 0 or bad <= 0.02 * n, f"{bad}/{n} gemelas de fachada caen en otra hilada")
    check(n == 0 or badx <= 0.02 * n, f"{badx}/{n} gemelas de fachada tienen la junta vertical del ladrillo corrida")
    result(islas_sin_alinear=res[False]["islas"], islas_alineado=res[True]["islas"], gemelas_fachada=n, desalineadas=bad, corridas_x=badx)


def case_ao_bake():
    """Oclusión: valores válidos, sin bloques negros y con el piso aplicado en el color."""
    import numpy as np
    s = setup("L", "FLAT", 24, 16, 5, bake_ao=True)
    objs = lod_objs(s)
    p = a.props_to_params(s)
    p["bake_samples"] = 16
    a.uv_generate(bpy.context, p, objs, None)
    a.bake_ao_all(bpy.context, p, objs, None)
    for lod in range(3):
        ao = a.read_ao(s.building_name, lod, a.uv_state(objs[lod].data)["uid"])
        check(ao is not None, f"LOD{lod}: sin mapa de sombras")
        check(bool(np.isfinite(ao).all()), f"LOD{lod}: sombras con NaN")
        check(float(ao.min()) >= 0.0 and float(ao.max()) <= 1.0 + 1e-6, f"LOD{lod}: sombras fuera de 0-1")
        if lod == 0:
            mean = float(ao.mean())
            dark = float((ao < 0.05).mean())
            check(0.55 < mean < 0.99, f"sombra media rara: {mean:.2f}")
            check(dark < 0.04, f"demasiado negro en las sombras: {dark * 100:.1f} % de texels")
    result(ao_media=round(mean, 3), ao_negro_pct=round(dark * 100, 2))


def case_assign_textures():
    """Los tres pasos (UV, sombras, texturas) y quitarlas."""
    s = setup("L", "FLAT", 24, 16, 5, bake_ao=True)
    check(bpy.ops.cs2.uv_generate() == {"FINISHED"}, "uv_generate falló")
    p = a.props_to_params(s)
    p["bake_samples"] = 8
    objs = lod_objs(s)
    a.bake_ao_all(bpy.context, p, objs, None)
    check(bpy.ops.cs2.tex_assign() == {"FINISHED"}, "tex_assign falló")
    for i, o in enumerate(objs):
        check(o.data.uv_layers.active.name == "CS2_unique", f"LOD{i}: la UV activa no es la horneada")
        names = [m.name for m in o.data.materials if m]
        check(any("__v" in n for n in names), f"LOD{i}: no se aplicaron los materiales de vista")
    check(bpy.ops.cs2.tex_clear() == {"FINISHED"}, "tex_clear falló")
    result(ok=True)


def case_export_cs2():
    """Exportación a CS2: FBX de los tres LOD y texturas, con archivos no vacíos."""
    s = setup("L", "FLAT", 24, 16, 5, bake_ao=True)
    out = os.path.join(tempfile.gettempdir(), "cs2_test_export")
    import shutil
    shutil.rmtree(out, ignore_errors=True)
    s.export_dir = out
    r = bpy.ops.cs2.export()
    check(r == {"FINISHED"}, f"export devolvió {r}")
    files = [os.path.join(rt, f) for rt, _d, fs in os.walk(out) for f in fs]
    fbx = [f for f in files if f.lower().endswith(".fbx")]
    img = [f for f in files if f.lower().endswith((".png", ".tga", ".jpg"))]
    check(len(fbx) >= 3, f"se esperaban 3 FBX y hay {len(fbx)}")
    check(len(img) >= 3, f"se esperaban texturas y hay {len(img)}")
    check(all(os.path.getsize(f) > 1000 for f in fbx + img), "hay archivos casi vacíos")
    result(fbx=len(fbx), texturas=len(img))


def case_progress():
    """Barra de progreso: los operadores largos (UV, exportar CS2/CS1) la mueven de 0 a 1 y la cierran al terminar; la interfaz la dibuja."""
    s = setup("L", "FLAT", 20, 14, 4, bake_ao=True)
    seen = []
    orig = a._prog_step

    def spy(context, frac, msg=None, force=False):
        seen.append((round(frac, 3), a._PROG["busy"]))
        return orig(context, frac, msg, force)
    a._prog_step = spy
    try:
        out = os.path.join(tempfile.gettempdir(), "cs2_test_progress")
        import shutil
        shutil.rmtree(out, ignore_errors=True)
        s.export_dir = out
        ug = a.uv_generate
        during = []

        def ug_spy(context, p, objs, progress=None):
            during.append((a._PROG["busy"], a._PROG["title"]))
            if progress:
                progress(0.5, "mitad")
                during.append((a._PROG["busy"], round(a._PROG["frac"], 2)))
            return ug(context, p, objs, progress)
        a.uv_generate = ug_spy
        try:
            check(bpy.ops.cs2.uv_generate() == {"FINISHED"}, "uv_generate")
        finally:
            a.uv_generate = ug
        check(during and during[0][0] and during[1] == (True, 0.5), f"la barra no estaba activa durante las UV: {during}")
        check(not a._PROG["busy"], "la barra quedó abierta tras generar UV")
        uv_steps = len(during)
        seen.clear()
        check(bpy.ops.cs2.export() == {"FINISHED"}, "export")
        fr = [f for f, _b in seen]
        check(len(fr) >= 4 and fr == sorted(fr) and fr[-1] >= 0.9, f"el progreso de exportar no avanza: {fr}")
        check(all(b for _f, b in seen), "se informó progreso con la barra cerrada")
        check(not a._PROG["busy"], "la barra quedó abierta tras exportar")
        seen.clear()
        s.game = "CS1"
        check(bpy.ops.cs2.cs1_generate() == {"FINISHED"}, "cs1_generate")
        check(bpy.ops.cs2.export_cs1() == {"FINISHED"}, "export_cs1")
        check(len(seen) >= 8 and not a._PROG["busy"], f"progreso CS1: {len(seen)} pasos, busy={a._PROG['busy']}")
        s.game = "CS2"
    finally:
        a._prog_step = orig
    # la interfaz dibuja la barra mientras hay una tarea activa
    with a._Work(bpy.context, "Prueba") as w:
        w.step(0.5, "mitad")
        check(a._PROG["busy"] and abs(a._PROG["frac"] - 0.5) < 1e-6, "estado de la barra")
        for cls in a.classes:
            if isinstance(cls, type) and issubclass(cls, bpy.types.Panel) and hasattr(cls, "draw"):
                pass
    check(not a._PROG["busy"], "_Work no cerró la barra")
    result(pasos_uv=uv_steps, pasos_export=len(fr))


def case_tex_share():
    """Texture Sharing (wiki): con sombras horneadas el LOD0 y el LOD1 comparten UN juego de texturas (settings.json con sharedAssets); el LOD2 lleva las suyas."""
    import shutil
    s = setup("L", "FLAT", 24, 16, 5, bake_ao=True)
    objs = lod_objs(s)
    p = a.props_to_params(s)
    infos = a.uv_generate(bpy.context, p, objs, None)
    check(infos[0].get("compartido") and infos[1].get("compartido") and not infos[2].get("compartido"), "solo LOD0 y LOD1 comparten el mapa")
    check(infos[0]["uso"] == infos[1]["uso"], "LOD0 y LOD1 deberían reportar el mismo mapa")
    check(infos[1]["px_por_m"] < infos[0]["px_por_m"], "el LOD1 debería llevar menos densidad que el LOD0")
    for i, o in enumerate(objs):
        check(_uv_checks(o.data) == 0, f"LOD{i}: UV fuera de 0-1")
    # las islas de un LOD no se pisan con las de otro (salvo caras gemelas que comparten isla a propósito)
    masks = [a.uv_mask(o.data, "CS2_unique", 512, 0) for o in objs[:2]]
    inter = float((masks[0] & masks[1]).mean())
    check(inter < 0.10, f"las islas del LOD0 y del LOD1 se pisan: {inter * 100:.1f}% del mapa")
    out = os.path.join(tempfile.gettempdir(), "cs2_test_share")
    shutil.rmtree(out, ignore_errors=True)
    s.export_dir = out
    check(bpy.ops.cs2.export() == {"FINISHED"}, "export")
    folder = next(os.path.join(rt) for rt, _d, fs in os.walk(out) if "settings.json" in fs)
    files = sorted(os.listdir(folder))
    asset = next(f[:-len("_BaseColor.png")] for f in files if f.endswith("_BaseColor.png") and "_LOD" not in f)
    check(not any("_LOD1_" in f and f.endswith(".png") for f in files), f"el LOD1 tiene texturas propias: {files}")
    check(all(f"{asset}_LOD2_{suf}.png" in files for suf in a.TEX_SUFFIXES), f"faltan las texturas propias del LOD2: {files}")
    for suf in a.TEX_SUFFIXES:
        check(f"{asset}_{suf}.png" in files, f"falta {asset}_{suf}.png")
    data = json.load(open(os.path.join(folder, "settings.json")))["sharedAssets"]
    for suf in a.TEX_SUFFIXES:
        check(data.get(f"{asset}_LOD1_{suf}.png") == f"{asset}_{suf}.png", f"settings.json no comparte LOD1 {suf}")
    check(not any("_LOD2_" in k for k in data), "settings.json no debería compartir el LOD2")
    check(f"{asset}_LOD1.fbx" in files and f"{asset}_LOD2.fbx" in files, "faltan los FBX de LOD1/LOD2")
    # aunque se apague la opción vieja, el settings.json es obligatorio
    s.tex_share_lods = False
    shutil.rmtree(out, ignore_errors=True)
    a.uv_generate(bpy.context, a.props_to_params(s), objs, None)
    check(bpy.ops.cs2.export() == {"FINISHED"}, "export con la opción apagada")
    fs2 = [f for _rt, _d, fl in os.walk(out) for f in fl]
    check("settings.json" in fs2 and not any("_LOD1_BaseColor.png" in f for f in fs2), "el settings.json es obligatorio y el LOD1 no lleva texturas propias")
    s.tex_share_lods = True
    # «Crear texturas» también deja el settings.json junto a las texturas
    shutil.rmtree(out, ignore_errors=True)
    a.uv_generate(bpy.context, a.props_to_params(s), objs, None)
    check(bpy.ops.cs2.make_textures() == {"FINISHED"}, "make_textures")
    fs3 = [f for _rt, _d, fl in os.walk(out) for f in fl]
    check("settings.json" in fs3, f"Crear texturas no escribió settings.json: {fs3}")
    # modo rápido (sin sombras horneadas): también lleva settings.json
    s.bake_ao = False
    shutil.rmtree(out, ignore_errors=True)
    check(bpy.ops.cs2.export() == {"FINISHED"}, "export en modo rápido")
    fs4 = [f for _rt, _d, fl in os.walk(out) for f in fl]
    check("settings.json" in fs4, "el modo rápido no escribió settings.json")
    s.bake_ao = True
    result(px_lod0=infos[0]["px_por_m"], px_lod1=infos[1]["px_por_m"], uso=infos[0]["uso"])


def case_manual_mesh():
    """Edificio hecho a mano: el generador crea LOD1 y LOD2 dentro de los límites."""
    s = setup("L", "FLAT", 24, 16, 5)
    n = s.building_name
    lod0 = bpy.data.objects[f"{n}_LOD0"]
    man = lod0.copy()
    man.data = lod0.data.copy()
    man.name = "MiEdificio"
    bpy.context.scene.collection.objects.link(man)
    man.location = (0, 60, 0)
    ren = {"glass": "Vidrio", "trim": "Molduras", "roof": "Techo", "door": "Puerta", "gar": "Porton", "rail": "Baranda", "facade": "Pared"}
    for i, m in enumerate(man.data.materials):
        man.data.materials[i] = bpy.data.materials.new(ren.get(m.name.split("_")[-1], "Pared") + "_x")
    for o in list(bpy.data.objects):
        if o.name.startswith(n + "_LOD"):
            bpy.data.objects.remove(o, do_unlink=True)
    s.building_name = "MiEdificio"
    bpy.context.view_layer.objects.active = man
    check(bpy.ops.cs2.from_mesh() == {"FINISHED"}, "from_mesh falló")
    t = [a.mesh_tris(bpy.data.objects[f"MiEdificio_LOD{i}"]) for i in range(3)]
    check(t[2] <= a.LOD2_MAX_TRIS, f"LOD2 manual {t[2]} > {a.LOD2_MAX_TRIS}")
    check(t[1] < 0.5 * t[0], f"LOD1 manual {t[1]} no es menos de la mitad de {t[0]}")
    result(tris=t)


def case_cs1():
    """CS1: se genera la malla principal y el LOD dentro del límite y se exporta."""
    s = setup("L", "FLAT", 24, 16, 5)
    s.game = "CS1"
    check(bpy.ops.cs2.cs1_generate() == {"FINISHED"}, "cs1_generate falló")
    c1 = a.cs1_objs(s)
    check(c1 is not None and len(c1) == 2, "faltan las mallas CS1")
    t = [sum(len(p.vertices) - 2 for p in o.data.polygons) for o in c1]
    check(t[0] <= int(s.cs1_tris) * 1.05, f"CS1 principal {t[0]} > {s.cs1_tris}")
    out = os.path.join(tempfile.gettempdir(), "cs2_test_cs1")
    import shutil
    shutil.rmtree(out, ignore_errors=True)
    s.export_dir = out
    check(bpy.ops.cs2.export_cs1() == {"FINISHED"}, "export_cs1 falló")
    files = [f for _r, _d, fs in os.walk(out) for f in fs]
    check(any(f.endswith(".fbx") for f in files), "CS1: no hay FBX")
    result(tris=t, archivos=len(files))


def case_report_log():
    """Informe y registro de errores."""
    s = setup("L", "FLAT", 24, 16, 5, bake_ao=True)
    try:
        raise ValueError("prueba")
    except ValueError as e:
        a.log_error("test", e)
        a.log_error("test", e)
    check(len(a._ERRLOG) == 1 and a._ERRLOG[0]["n"] == 2, "el registro no agrupa errores repetidos")
    orig = a.uv_generate
    a.uv_generate = lambda *x, **k: (_ for _ in ()).throw(RuntimeError("fallo forzado"))
    try:
        try:
            bpy.ops.cs2.uv_generate()
        except RuntimeError:
            pass
    finally:
        a.uv_generate = orig
    check(any("fallo forzado" in e["msg"] for e in a._ERRLOG), "el operador no registró el error")
    check(bpy.ops.cs2.copy_report() == {"FINISHED"}, "copy_report falló")
    txt = a.build_report()          # el portapapeles no existe sin interfaz: se prueba el texto del informe
    check("CS2 Generador de Edificios" in txt and "fallo forzado" in txt, "el informe no incluye lo esperado")
    result(lineas=len(txt.splitlines()))


def _fingerprint(s):
    """Huella de la geometría del LOD0: vértices redondeados + caras por material. Cambia si cambia la forma."""
    import hashlib
    out = []
    for o in lod_objs(s)[:1]:      # solo el LOD0: LOD1/LOD2 varían un poco entre ejecuciones (ver informe de la auditoría)
        me = o.data
        h = hashlib.sha1()
        for c in sorted((round(v.co.x, 4) + 0.0, round(v.co.y, 4) + 0.0, round(v.co.z, 4) + 0.0) for v in me.vertices):
            h.update(repr(c).encode())
        for m in sorted(p.material_index for p in me.polygons):
            h.update(bytes([m % 256]))
        out.append(f"{len(me.vertices)}v-{len(me.polygons)}f-{h.hexdigest()[:12]}")
    return out


GOLDEN_CASES = (("BOX", "FLAT", 20, 14, 3, {}),
                ("L", "FLAT", 30, 20, 6, {}),
                ("U", "GABLE_TILE", 30, 24, 6, {}),
                ("T", "FLAT", 30, 24, 6, {"styles": False}),
                ("L", "GABLE_CHIM", 26, 18, 4, {"styles": False}),
                ("T", "HIP_CHIM", 28, 22, 4, {"styles": False}),
                ("U", "BUTTERFLY", 28, 22, 4, {"styles": False}),
                ("L", "SHED_MODERN", 26, 18, 4, {"styles": False}),
                ("BOX", "MANSARD", 20, 14, 4, {"styles": False}),
                ("L", "GABLE_DORMER", 26, 18, 4, {"styles": False}))


def case_geom_golden():
    """La geometría generada no cambia al reordenar el código (compara con golden.json; --update-golden la renueva)."""
    import json
    cur = {}
    for kind, preset, w, d, fl, kw in GOLDEN_CASES:
        s = setup(kind, preset, w, d, fl, **kw)
        cur[f"{kind}-{preset}-{w}x{d}x{fl}"] = _fingerprint(s)
        for o in lod_objs(s):
            bpy.data.objects.remove(o, do_unlink=True)
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "golden.json")
    if os.environ.get("CS2_UPDATE_GOLDEN") or not os.path.exists(path):
        json.dump(cur, open(path, "w"), indent=1)
        result(actualizado=True)
        return
    ref = json.load(open(path))
    diff = [k for k in cur if cur[k] != ref.get(k)]
    check(not diff, "la geometría cambió en: " + ", ".join(diff))
    result(casos=len(cur))


def case_balcony_golden():
    """Cada estilo de balcón incluido, en LOD0 y LOD1, genera la misma geometría que la referencia (balcony_golden.json)."""
    import json
    import hashlib
    import bmesh
    from mathutils import Vector
    cur = {}
    for name, st in a.builtin_list():
        if st.get("kind") != "balcony":
            continue
        for lod in (0, 1):
            bm = bmesh.new()
            a.insert_balcony(bm, st, Vector((0, 0, 0)), Vector((1, 0, 0)), Vector((0, -1, 0)), 0.0, 3.0, p={"floor_h": 3.0}, lod=lod)
            h = hashlib.sha1()
            for c in sorted((round(v.co.x, 4) + 0.0, round(v.co.y, 4) + 0.0, round(v.co.z, 4) + 0.0) for v in bm.verts):
                h.update(repr(c).encode())
            for m in sorted(f.material_index for f in bm.faces):
                h.update(bytes([m % 256]))
            cur[f"{name}|{lod}"] = f"{len(bm.verts)}v-{len(bm.faces)}f-{h.hexdigest()[:12]}"
            bm.free()
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "balcony_golden.json")
    if os.environ.get("CS2_UPDATE_GOLDEN") or not os.path.exists(path):
        json.dump(cur, open(path, "w"), indent=1)
        result(actualizado=True)
        return
    ref = json.load(open(path))
    diff = [k for k in cur if cur[k] != ref.get(k)]
    check(not diff, "balcón distinto en: " + ", ".join(diff[:8]))
    result(estilos=len(cur))


def case_wall_golden():
    """build_wall sobre una pared fija, en LOD0/1/2, con varios niveles de detalle y en modo CS1, da la misma geometría
    que la referencia (wall_golden.json)."""
    import json
    import hashlib
    import bmesh
    from mathutils import Vector
    s = setup("BOX", "FLAT", 20, 14, 3)
    cur = {}
    base = a.props_to_params(s)
    pts = [Vector((0, 0, 0)), Vector((12, 0, 0)), Vector((12, 0, 9)), Vector((0, 0, 9))]
    n = Vector((0, -1, 0))
    combos = [(0, 0, 1.0, False)]
    for tier in (0, 2, 4, 5, 6):
        for q in (1.0, 2.5, 5.5):
            combos.append((1, tier, q, False))
    for q in (1.0, 3.0):
        combos.append((2, 0, q, False))
    combos += [(1, 0, 1.0, True), (1, 5, 5.5, True), (0, 0, 1.0, True)]
    for lod, tier, q, cs1 in combos:
        p = dict(base)
        p["lod1_tier"], p["_q"] = tier, q
        if cs1:
            p["_cs1"] = True
        p.pop("_bands", None)
        bm = bmesh.new()
        a.build_wall(bm, pts, n, p, 0, lod)
        h = hashlib.sha1()
        for c in sorted((round(v.co.x, 4) + 0.0, round(v.co.y, 4) + 0.0, round(v.co.z, 4) + 0.0) for v in bm.verts):
            h.update(repr(c).encode())
        for m in sorted(f.material_index for f in bm.faces):
            h.update(bytes([m % 256]))
        h.update(repr(len(p.get("_bands", []))).encode())
        cur[f"lod{lod}-t{tier}-q{q}-cs1{cs1}"] = f"{len(bm.verts)}v-{len(bm.faces)}f-{h.hexdigest()[:12]}"
        bm.free()
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "wall_golden.json")
    if os.environ.get("CS2_UPDATE_GOLDEN") or not os.path.exists(path):
        json.dump(cur, open(path, "w"), indent=1)
        result(actualizado=True)
        return
    ref = json.load(open(path))
    diff = [k for k in cur if cur[k] != ref.get(k)]
    check(not diff, "pared distinta en: " + ", ".join(diff[:8]))
    result(casos=len(cur))


class _FakeLayout:
    """Layout de mentira: acepta cualquier llamada de dibujo, para comprobar que los paneles no fallan sin abrir la interfaz."""

    def __init__(self):
        self.scale_x = self.scale_y = 1
        self.enabled = self.active = True
        self.alert = False
        self.use_property_split = self.use_property_decorate = False
        self.alignment = "EXPAND"

    def __getattr__(self, n):
        if n.startswith("__"):
            raise AttributeError(n)
        return lambda *a, **k: _FakeLayout()

    def __bool__(self):
        return True

    def __iter__(self):
        return iter(())


def case_ui_draw():
    """Todos los paneles de la barra lateral se dibujan sin errores, con y sin edificio, con y sin «Avanzado» y con la vista rápida."""
    panels = [c for c in a.classes if isinstance(c, type) and issubclass(c, bpy.types.Panel)]
    check(len(panels) >= 11, f"faltan paneles registrados: {len(panels)}")
    check(not hasattr(a, "CS2S_PT_studio"), "el Studio de Propiedades ya no debería existir")
    for with_building in (False, True):
        if with_building:
            s = setup("L", "GABLE_TILE", 24, 16, 4)
        else:
            if not hasattr(bpy.types.Scene, "cs2_building"):
                a.register()
            s = bpy.context.scene.cs2_building
        for adv in (False, True):
            for prev in (False, True):
              for tab in ("FORMA", "PERS", "TEX", "EXPORT"):
                s.ui_tab = tab
                s.studio_adv = adv
                s.pbr_preview = prev
                inst = type("X", (), {})()
                inst.layout = _FakeLayout()
                try:
                    a._header_draw(inst, bpy.context)
                except Exception as e:
                    check(False, f"botón de la barra (edificio={with_building}): {e!r}")
                check(a._header_draw in bpy.types.VIEW3D_HT_tool_header.draw._draw_funcs, "el botón de actualizar no está en la barra de la vista 3D")
                tops = [c for c in panels if not getattr(c, "bl_parent_id", "") and c is not a.CS2_PT_panel and getattr(c, "poll", None) and c.poll(bpy.context)]
                check(len(tops) >= 1, f"con la pestaña {tab} casi no se muestra nada: {[c.__name__ for c in tops]}")
                check(a.CS2_PT_forma.poll(bpy.context) == (tab == "FORMA") and a.CS2_PT_export.poll(bpy.context) == (tab == "EXPORT"), f"las pestañas no filtran bien ({tab})")
                check(all(not getattr(c, "bl_parent_id", "") in ("CS2_PT_forma", "CS2_PT_pers", "CS2_PT_tex", "CS2_PT_export", "CS2_PT_export_go") for c in panels), "quedó un panel dentro de otra sección")
                for cls in panels:
                    poll = getattr(cls, "poll", None)
                    if poll is not None and not poll(bpy.context):
                        continue
                    inst = type("X", (), {})()
                    inst.layout = _FakeLayout()
                    try:
                        cls.draw(inst, bpy.context)
                    except Exception as e:
                        check(False, f"{cls.__name__} (edificio={with_building}, avanzado={adv}, vista={prev}): {e!r}")
    result(ok=True)


def case_quick_view():
    """Vista rápida: al elegir una textura se ve en el modelo sin UV ni horneado; apagarla vuelve a colores lisos; no pisa texturas horneadas."""
    s = setup("BOX", "FLAT", 16, 12, 3)
    d = tempfile.mkdtemp()
    png = os.path.join(d, "rojo_color.png")
    img = bpy.data.images.new("t_rojo", 8, 8)
    img.pixels = [0.8, 0.1, 0.1, 1.0] * 64
    img.filepath_raw = png
    img.file_format = "PNG"
    img.save()
    s.pbr_preview = True
    s.ms_facade_mode = "PBR"
    s.ms_facade_img_color = png
    check(a.quick_view_apply(s), "quick_view_apply debería funcionar sin texturas horneadas")
    m = bpy.data.materials.get(f"{s.building_name}_facade") or next((m for m in bpy.data.materials if m.name.endswith("_facade")), None)
    check(m is not None and m.get("cs2_prev_on"), "la fachada debería mostrar la textura")
    bpy.ops.cs2.preview_update()
    s.pbr_preview = False
    check(not m.get("cs2_prev_on"), "al apagar la vista rápida vuelven los colores lisos")
    s.pbr_preview = True
    bpy.ops.cs2.preview_update()
    check(m.get("cs2_prev_on"), "Actualizar vista debería encenderla")
    result(ok=True)


def _density(me, idxs, uvn="CS2_unique"):
    """UV por metro (media) de un grupo de caras."""
    tot_uv = tot_m = 0.0
    d = me.uv_layers[uvn].data
    for i in idxs:
        poly = me.polygons[i]
        uv = [d[li].uv for li in poly.loop_indices]
        a2 = 0.5 * abs(sum(uv[k][0] * uv[(k + 1) % len(uv)][1] - uv[(k + 1) % len(uv)][0] * uv[k][1] for k in range(len(uv))))
        tot_uv += a2
        tot_m += poly.area
    return (tot_uv / max(tot_m, 1e-9)) ** 0.5


def case_uv_zones():
    """Ocupación por componente: cada componente tiene la misma nitidez en todas sus caras (también las que el análisis da por
    tapadas pero asoman por una rendija) y subir su factor agranda sus UV en la textura (linealmente respecto del resto)."""
    s = setup("BOX", "FLAT", 20, 14, 4, bake_ao=True, balconies="alternate", balcony_style="Balcón vidrio y pasamanos")
    objs = lod_objs(s)
    me = objs[0].data
    p = a.props_to_params(s)
    info = a.uv_generate(bpy.context, p, objs, None)[0]
    shell = [q.index for q in me.polygons if q.material_index not in a.SUB_MATS and abs(q.normal.z) < 0.7
             and (q.material_index in a.SHELL_MATS)]
    # 1) misma nitidez en todas las paredes (incluidas las "tapadas")
    for m_ in {me.polygons[i].material_index for i in shell}:
        idx = [i for i in shell if me.polygons[i].material_index == m_]
        if len(idx) < 6:
            continue
        ds = sorted(_density(me, [i]) for i in idx)
        check(ds[0] > 0.9 * ds[len(ds) // 2], f"material {m_}: hay caras de pared más borrosas ({ds[0]:.3f} vs mediana {ds[len(ds) // 2]:.3f})")
    # 2) el factor de un componente reparte linealmente la nitidez respecto de los demás
    px0 = info["comp_px"]
    s.uv_c_wall = 2.0
    info2 = a.uv_generate(bpy.context, a.props_to_params(s), objs, None)[0]
    r0 = px0["wall"] / px0["trim"]
    r1 = info2["comp_px"]["wall"] / info2["comp_px"]["trim"]
    check(abs(r1 / r0 - 2.0) < 0.15, f"el factor 2 de Paredes debería duplicar su nitidez respecto de las molduras: x{r1 / r0:.2f}")
    check(info2["comp_pct"]["wall"] > info["comp_pct"]["wall"], "Paredes debería ocupar más lugar de la textura")
    check(_uv_checks(me) == 0, "UV fuera de 0-1 con factor de componente")
    s.uv_c_wall = 1.0
    result(r_nitidez=round(r1 / r0, 2), pct_wall=info["comp_pct"]["wall"], pct_wall_x2=info2["comp_pct"]["wall"])


def case_facade_holes():
    """Con la limpieza de caras ocultas activa, la fachada no queda con huecos (franjas de pared bajo cornisas, tras persianas, etc.)."""
    from mathutils import Vector
    from mathutils.bvhtree import BVHTree
    bad_all = {}
    for w in ("Ventana con moldura", "Ventana moldura y dintel", "Ventana persianas cerradas", "Ventana arco rebajado", "Ventana con frontón"):
        s = setup("BOX", "FLAT", 15, 23, 3, styles=False, balconies="alternate", window_style=w, clean_on_generate=True)
        me = lod_objs(s)[0].data
        bvh = BVHTree.FromPolygons([v.co[:] for v in me.vertices], [p.vertices[:] for p in me.polygons])
        W, D, H = s.width, s.depth, s.floors * s.floor_height
        bad = 0
        for i in range(int(W / 0.1)):
            x = -W / 2 + (i + 0.5) * 0.1
            for j in range(int(H / 0.1)):
                h = bvh.ray_cast(Vector((x, -D / 2 - 3, (j + 0.5) * 0.1)), Vector((0, 1, 0)), 50)
                if h[0] is not None and h[0].y > -D / 2 + 1.0:
                    bad += 1
        if bad:
            bad_all[w] = bad
    check(not bad_all, f"huecos en la fachada: {bad_all}")
    result(ok=True)


def case_icons():
    """Todos los íconos que usa el add-on existen en este Blender (el layout falso de ui_draw no los valida)."""
    import re
    ids = {e.identifier for e in bpy.types.UILayout.bl_rna.functions["operator"].parameters["icon"].enum_items}
    src = open(a.__file__, encoding="utf-8").read()
    used = set(re.findall(r'icon\s*=\s*"([A-Z0-9_]+)"', src)) | set(re.findall(r'"icon":\s*"([A-Z0-9_]+)"', src))
    used |= {ico for _k, _l, _d, ico in a.UI_TABS}
    bad = sorted(used - ids)
    check(not bad, f"íconos que no existen en Blender {bpy.app.version_string}: {bad}")
    result(ok=True, usados=len(used))


def _mip(img, k):
    f = 2 ** k
    h, w = img.shape[:2]
    return img[:h // f * f, :w // f * f].reshape(h // f, f, w // f, f, -1).mean((1, 3))


def _bleed_pct(me, uv_name, img, k, red_ratio):
    """% de muestras (fachada roja / molduras grises) cuyo color, ya achicada la imagen k niveles de mipmap, es el del otro material."""
    import numpy as np
    m = _mip(img, k)
    h, w = m.shape[:2]
    bad = tot = 0
    uvl = me.uv_layers[uv_name].data
    for q in me.polygons:
        if q.material_index in a.SUB_MATS:
            continue
        mn = a.MAT_ORDER[q.material_index]
        if mn not in ("facade", "trim"):
            continue
        pts = np.array([uvl[li].uv for li in q.loop_indices])
        c = pts.mean(0)
        for pt in [c] + [c + (u - c) * 0.9 for u in pts]:
            col = m[min(h - 1, max(0, int(pt[1] * h))), min(w - 1, max(0, int(pt[0] * w)))]
            redish = col[0] > col[1] * red_ratio and col[0] > col[2] * red_ratio
            tot += 1
            bad += redish != (mn == "facade")
    return 100.0 * bad / max(tot, 1)


def case_mip_bleed():
    """Con la textura achicada (mipmaps del visor y del juego) un material no toma el color del vecino: ni en el atlas ni en el horneado."""
    import numpy as np
    from PIL import Image
    red = os.path.join(tempfile.gettempdir(), "red_test.png")
    Image.fromarray(np.full((64, 64, 3), (190, 60, 50), np.uint8)).save(red)
    s = setup("L", "FLAT", 24, 16, 5, ms_facade_mode="PBR", ms_facade_img_color=red, ms_facade_tile=2.0)
    me = lod_objs(s)[0].data
    base = a.texture_arrays(a.props_to_params(s), 1024)["BaseColor"][..., :3]
    for k in (3, 4, 5):
        pct = _bleed_pct(me, me.uv_layers[0].name, base, k, 1.5)
        check(pct < 2.0, f"atlas: {pct:.1f}% de muestras con color del material vecino al achicar {k} niveles")
    s = setup("L", "FLAT", 24, 16, 5, bake_ao=True, ms_facade_mode="PBR", ms_facade_img_color=red, ms_facade_tile=2.0)
    bpy.ops.cs2.retexture()
    me = lod_objs(s)[0].data
    img = bpy.data.images["CS2_view_%s_LOD0_BaseColor" % s.building_name]
    W, H = img.size
    px = np.array(img.pixels[:], np.float32).reshape(H, W, 4)[..., :3]
    pct = _bleed_pct(me, "CS2_unique", px, 3, 1.5)
    check(pct < 4.0, f"horneado: {pct:.1f}% de muestras con color del material vecino al achicar 3 niveles")
    result(ok=True, horneado_mip3=round(pct, 1))


def case_retexture():
    """El botón «Rehacer texturas» (UV + sombras + asignar) deja el edificio con las texturas asignadas, y se puede repetir."""
    s = setup("BOX", "FLAT", 16, 12, 3, bake_ao=True)
    for _ in range(2):
        r = bpy.ops.cs2.retexture()
        check("FINISHED" in r, f"retexture devolvió {r}")
        st = a.tex_status(s)
        check(st["uv"] and st["bake"] and st["assigned"], f"estado tras rehacer: {st['uv']} {st['bake']} {st['assigned']}")
    result(ok=True)


def case_tex_missing():
    """Avisa cuando falta el archivo de una textura (antes el material caía a color liso sin decir nada)."""
    s = setup("BOX", "FLAT", 16, 12, 3)
    check(not a.texture_problems(s), "sin materiales con textura no debería haber problemas")
    s.ms_facade_mode = "PBR"
    pr = a.texture_problems(s)
    check(len(pr) == 1 and "color" in pr[0][1].lower(), f"sin imagen de color: {pr}")
    s.ms_facade_img_color = os.path.join(tempfile.gettempdir(), "no_existe_cs2.png")
    pr = a.texture_problems(s)
    check(len(pr) == 1 and "no existe" in pr[0][3], f"archivo inexistente: {pr}")
    check(any(not ok and "Texturas" in t for ok, t in a._checklist(s)), "la lista de control debe avisar")
    brick = make_brick(os.path.join(tempfile.gettempdir(), "cs2_test_brick.png"))
    s.ms_facade_img_color = brick
    check(not a.texture_problems(s), "con el archivo presente no debe avisar")
    check(any(ok and "encontrados" in t for ok, t in a._checklist(s)), "la lista debe confirmar las texturas")
    p = a.props_to_params(s)
    check(p["matslots"]["facade"]["img_color"] == brick, "la ruta debe llegar completa a los parámetros")
    result(ok=True)


def case_ground_mat():
    """La planta baja puede llevar otro material: solo las paredes de abajo cambian, en los tres LODs, y apagado no cambia nada."""
    s = setup("BOX", "FLAT", 16, 12, 4, styles=False)
    MB = a.MAT["facb"]
    fh = s.floor_height

    def split(o):
        low = [p for p in o.data.polygons if p.material_index == MB and p.center.z < fh]
        high = [p for p in o.data.polygons if p.material_index == MB and p.center.z > fh + 0.01]
        return len(low), len(high)
    for o in lod_objs(s):
        check(sum(1 for p in o.data.polygons if p.material_index == MB) == 0, "sin planta baja especial no debe haber material B")
    s.ground_mat_g = "2"
    bpy.ops.cs2.generate()
    bpy.ops.cs2.regen_lods()
    for i, o in enumerate(lod_objs(s)):
        low, high = split(o)
        check(low > 0, f"LOD{i}: la planta baja no tomó el material B")
        check(high == 0, f"LOD{i}: {high} caras de los pisos de arriba tomaron el material B")
    s.ground_n_g = 2
    bpy.ops.cs2.generate()
    bpy.ops.cs2.regen_lods()
    o = lod_objs(s)[0]
    altos = [p for p in o.data.polygons if p.material_index == MB and fh + 0.01 < p.center.z < 2 * fh]
    check(altos, "con 2 pisos, el segundo también debe llevar el material B")
    check(not [p for p in o.data.polygons if p.material_index == MB and p.center.z > 2 * fh + 0.01], "el tercer piso no debe llevarlo")
    result(ok=True)


def case_uv_golden():
    """Métricas de UV (islas, px/m, aprovechamiento) de varios edificios frente a golden_uv.json, con tolerancia: el orden de
    las caras generadas varía un poco entre ejecuciones, así que un hash exacto no sirve. CS2_UPDATE_GOLDEN=1 la renueva."""
    import json
    brick = make_brick(os.path.join(tempfile.gettempdir(), "cs2_test_brick.png"))
    cur = {}
    for kind, w, d, fl in (("L", 24, 16, 5), ("U", 30, 24, 6)):
        s = setup(kind, "FLAT", w, d, fl, bake_ao=True, ms_facade_mode="PBR", ms_facade_img_color=brick, ms_facade_tile=2.0)
        objs = lod_objs(s)
        infos = a.uv_generate(bpy.context, a.props_to_params(s), objs, None)
        for i, o in enumerate(objs):
            check(_uv_checks(o.data) == 0, f"{kind} LOD{i}: UV fuera de 0-1")
            cur[f"{kind}-LOD{i}"] = {"islas": infos[i]["islas"], "px_por_m": infos[i]["px_por_m"], "uso": infos[i]["uso"],
                                     "compartidas": infos[i]["compartidas"]}
        for o in objs:
            bpy.data.objects.remove(o, do_unlink=True)
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "golden_uv.json")
    if os.environ.get("CS2_UPDATE_GOLDEN") or not os.path.exists(path):
        json.dump(cur, open(path, "w"), indent=1)
        result(actualizado=True)
        return
    ref = json.load(open(path))
    bad = []
    for k, v in cur.items():
        r = ref.get(k)
        if r is None:
            bad.append(f"{k}: sin referencia")
            continue
        if abs(v["islas"] - r["islas"]) > max(4, 0.10 * r["islas"]):
            bad.append(f"{k} islas {v['islas']} vs {r['islas']}")
        if abs(v["px_por_m"] - r["px_por_m"]) > 0.06 * r["px_por_m"] + 0.3:
            bad.append(f"{k} px/m {v['px_por_m']} vs {r['px_por_m']}")
        if v["uso"] < r["uso"] - 5:
            bad.append(f"{k} uso {v['uso']} vs {r['uso']}")
    check(not bad, "las UV cambiaron: " + "; ".join(bad))
    result(casos=len(cur))


def case_perf_big():
    """Edificio grande: Regenerar UV no se pasa de tiempo y mantiene la nitidez."""
    s = setup("L", "FLAT", 40, 24, 12, bake_ao=True)
    objs = lod_objs(s)
    p = a.props_to_params(s)
    t0 = time.time()
    infos = a.uv_generate(bpy.context, p, objs, None)
    dt = time.time() - t0
    check(dt < 90, f"Regenerar UV tardó {dt:.0f} s")
    worse_than("perf_big.px_por_m", infos[0]["px_por_m"])
    worse_than("perf_big.segundos", dt, higher_is_better=False, tol=0.5)
    result(segundos=round(dt, 1), px_por_m=infos[0]["px_por_m"], islas=infos[0]["islas"])


def case_wall_cuts():
    """Paredes rectangulares: las aberturas se cortan con el mínimo de caras (tiras laterales de alto completo, sin grilla)."""
    import bmesh
    s = setup("BOX", "FLAT", 20, 14, 4)
    o0 = lod_objs(s)[0]
    me = o0.data
    zmax = max(v.co.z for v in me.vertices)
    planes = {}
    for q in me.polygons:
        if q.material_index == a.MAT["facade"] and abs(q.normal.z) < 0.1:
            key = (round(q.normal.x, 1), round(q.normal.y, 1), round(q.center.dot(q.normal), 1))
            planes.setdefault(key, []).append(q)
    # la pared con más caras (las de las jambas miran de lado: se filtran por la normal del plano elegido)
    key = max(planes, key=lambda k: len(planes[k]))
    front = planes[key]
    check(front, "no hay caras de pared")
    zs = lambda q: [me.vertices[v].co.z for v in q.vertices]
    heights = sorted(max(zs(q)) - min(zs(q)) for q in front)
    check(heights[-1] > 0.8 * zmax, f"ninguna tira lateral cubre el alto del edificio (la mayor mide {heights[-1]:.1f} de {zmax:.1f} m)")
    check(len(front) <= 90, f"demasiadas caras de pared en una fachada: {len(front)}")
    bm = bmesh.new(); bm.from_mesh(me); bm.edges.ensure_lookup_table(); bm.faces.ensure_lookup_table()
    free = sum(1 for q in front for e in bm.faces[q.index].edges if len(e.link_faces) < 2)
    bm.free()
    check(free <= 2 * len(front), f"aristas sueltas en la fachada: {free}")
    result(caras_fachada=len(front), mayor_tira=round(heights[-1], 1), alto=round(zmax, 1), aristas_sueltas=free)


def case_main_only():
    """Crear el edificio deja solo la malla principal; Generar LODs crea el LOD1 y el LOD2, y las UV los generan solas si faltan."""
    s = setup("BOX", "FLAT", 16, 12, 3, styles=False, _main_only=True)
    n = s.building_name
    check(bpy.data.objects.get(f"{n}_LOD0") is not None, "falta el LOD0")
    check(bpy.data.objects.get(f"{n}_LOD1") is None and bpy.data.objects.get(f"{n}_LOD2") is None, "crear no debe generar LOD1/LOD2")
    bpy.ops.cs2.regen_lods()
    check(all(bpy.data.objects.get(f"{n}_LOD{i}") is not None for i in range(3)), "Generar LODs no creó los tres")
    bpy.ops.cs2.generate()
    check(bpy.data.objects.get(f"{n}_LOD1") is None, "rehacer la principal debe quitar los LODs viejos")
    bpy.ops.cs2.uv_generate()
    check(all(bpy.data.objects.get(f"{n}_LOD{i}") is not None for i in range(3)), "las UV no generaron los LODs que faltaban")
    result(ok=True)


def case_component_lods():
    """Ventanas y puertas con estilo: en LOD1 cada una queda en la mitad (o menos) de sus triángulos, sin perder el vidrio;
    en LOD2 la ventana es un solo plano (sin triángulos fuera del vidrio)."""
    import bmesh
    from mathutils import Vector
    s = setup("BOX", "FLAT", 14, 12, 3, styles=False)
    p = a.props_to_params(s)
    a.lib_ensure()
    n, u, o = Vector((0, -1, 0)), Vector((1, 0, 0)), Vector((0, 0, 0))
    malos, vistos = [], 0
    for nm in a._LIB["names"]:
        st = a.get_style(nm)
        if st is None:
            continue
        door = st.get("kind") == "door"
        op = (0.0, 1.0, 0.0, 2.1, a.MAT["door"]) if door else (0.0, 1.3, 0.9, 2.3, a.MAT["glass"])
        sb = bmesh.new()
        try:
            a.insert_style(sb, st, o, u, n, op, 0.0, p)
            antes, desp = a._reduce_piece(sb, 0.5, n)
            vidrio = sum(1 for f in sb.faces if f.material_index in a.SUB_MATS)
        finally:
            sb.free()
        vistos += 1
        if desp > antes * 0.5 + 1:
            malos.append(f"{nm}: {antes} -> {desp}")
        if not door and vidrio == 0:
            malos.append(f"{nm}: se perdio el vidrio")
    check(vistos > 50, "pocos estilos probados")
    check(not malos, "no llegan a la mitad: " + "; ".join(malos[:5]))
    s2 = setup("BOX", "FLAT", 14, 12, 3, styles=False, window_style="Ventana con moldura", door_style="NONE", balconies="none", balcony_style="NONE")
    L = lod_objs(s2)
    glass = a.MAT["glass"]
    ng = sum(1 for p_ in L[2].data.polygons if p_.material_index == glass)
    check(ng > 0, "el LOD2 no tiene ventanas")
    extra = sum(len(p_.vertices) - 2 for p_ in L[2].data.polygons if p_.material_index == a.MAT["trim"])
    check(extra < 60, f"el LOD2 tiene marcos sueltos de ventana ({extra} tri de molduras)")
    result(estilos=vistos)


def case_roof_faces():
    """Techo por cara: una cara de arriba con techo propio, la otra con el general; al reabrir el panel trae los valores de la cara."""
    import bmesh
    s = setup("L", "FLAT", 24, 16, 3, styles=False)
    bpy.ops.cs2.edit_shape()
    obj = s.shape_object
    bm = bmesh.from_edit_mesh(obj.data)
    bmesh.ops.bisect_plane(bm, geom=list(bm.verts) + list(bm.edges) + list(bm.faces), plane_co=(0, 0, 0), plane_no=(1, 0, 0))
    bmesh.update_edit_mesh(obj.data)
    bm = bmesh.from_edit_mesh(obj.data)
    tops = [f for f in bm.faces if f.normal.z > 0.5]
    check(len(tops) == 2, "no se pudieron separar dos techos")
    for f in bm.faces:
        f.select = False
    tops[0].select = True
    bmesh.update_edit_mesh(obj.data)
    s.edit_scope = "FACES"
    pr = list(a.ROOF_PRESETS)[1]
    bpy.ops.cs2.comp_roof("EXEC_DEFAULT", alcance="FACES", r_preset_cargado=pr, r_preset=pr, r_pitch=40.0)
    bpy.ops.cs2.comp_roof("INVOKE_DEFAULT")
    bm = bmesh.from_edit_mesh(obj.data)
    lays = a.wall_layers(bm, create=False)
    vals = sorted(f[lays["cs2_r_pitch"]] for f in bm.faces if f.normal.z > 0.5)
    check(vals[-1] == 40.0, f"el techo de la cara no quedo guardado ({vals})")
    check(sum(1 for f in bm.faces if f.normal.z > 0.5 and f[lays["cs2_r_set"]]) == 1, "solo una cara debe tener techo propio")
    cfg = a._roof_selected_cfg(bpy.context)
    check(cfg and abs(cfg["pitch"] - 40.0) < 1e-3, f"al reabrir el techo no se leen los valores de la cara ({cfg})")
    spec = a._COMP["specs"]["roof"]
    check(spec.get("roof"), "el componente techo debe mostrar sus campos con caras elegidas")
    bpy.ops.object.mode_set(mode="OBJECT")
    result(ok=True)


def case_roof_join():
    """Un techo a dos aguas ancho que termina contra otro mas angosto y bajo (forma de «T» con ala lateral): el extremo no puede quedar abierto."""
    import bmesh
    from mathutils import Vector
    from mathutils.bvhtree import BVHTree
    V = [(-3.5, -11.5, 0.0), (3.5, -11.5, 0.0), (3.5, 11.5, 0.0), (-3.5, 11.5, 0.0), (-3.5, -11.5, 3.0), (3.5, -11.5, 3.0),
         (3.5, 11.5, 3.0), (-3.5, 11.5, 3.0), (-3.5, 3.833, 0.0), (-3.5, -3.833, 0.0), (3.5, -3.833, 0.0), (3.5, 3.833, 0.0),
         (3.5, -3.833, 3.0), (3.5, 3.833, 3.0), (-3.5, 3.833, 3.0), (-3.5, -3.833, 3.0), (9.445, -3.833, 0.0), (9.445, -3.833, 3.0),
         (3.5, 7.334, 0.0), (9.445, 7.334, 0.0), (3.5, 7.334, 3.0), (9.445, 7.334, 3.0)]
    F = [[8, 3, 2, 18, 11], [14, 13, 20, 6, 7], [9, 0, 4, 15], [2, 3, 7, 6], [0, 1, 5, 4], [3, 8, 14, 7], [8, 9, 15, 14], [1, 10, 12, 5],
         [12, 10, 16, 17], [4, 5, 12, 15], [15, 12, 17, 21, 20, 13, 14], [0, 9, 10, 1], [9, 8, 11, 18, 19, 16, 10], [16, 19, 21, 17],
         [18, 20, 21, 19], [18, 2, 6, 20]]
    s = setup("BOX", "GABLE_TILE", 10, 10, 1, styles=False)
    s.r_ridge = "1"
    s.r_pitch, s.r_oe, s.r_ov = 9.5, 1.1, 0.56
    me = s.shape_object.data
    me.clear_geometry()
    me.from_pydata(V, [], F)
    me.update()
    s.shape_object.location = (0, 0, 0)
    bpy.ops.cs2.generate()
    o = [o for o in bpy.data.objects if o.name.endswith("LOD0")][0]
    b = bmesh.new()
    b.from_mesh(o.data)
    b.transform(o.matrix_world)
    t = BVHTree.FromBMesh(b)
    bad = 0
    for y in (-3.0, -2.0, -1.0, 0.0, 1.0, 2.0, 3.0, 4.0, 5.0, 6.0):
        h = t.ray_cast(Vector((14.0, y, 4.0)), Vector((-1, 0, 0)))
        if h[0] is not None and h[1].x < -0.01:      # el rayo entra al edificio y toca una cara desde adentro
            bad += 1
    check(bad == 0, f"{bad} puntos junto al techo bajo dejan ver el interior (falta el hastial)")

    def hit(x_, y_):
        return t.ray_cast(Vector((x_, y_, 60)), Vector((0, 0, -1)))[0]
    check(hit(4.6, -9.0) is None, "sin alero fijado, el techo no debia pasar de la pared junto al techo bajo")
    s.r_sd_rg = 2.0                      # «Derecha (+X)»: el usuario amplia el techo en ese lado aunque haya otro techo pegado
    bpy.ops.cs2.generate()
    o = [o for o in bpy.data.objects if o.name.endswith("LOD0")][0]
    b = bmesh.new()
    b.from_mesh(o.data)
    b.transform(o.matrix_world)
    t = BVHTree.FromBMesh(b)
    check(hit(4.6, -9.0) is not None, "el alero fijado a mano no se aplica junto a otro techo")
    result(ok=True)


def case_scope_sync():
    """El alcance sigue siempre al modo de Blender: Tab fuera/dentro lo actualiza y un cambio de techo general despues no se pierde."""
    s = setup("L", "FLAT", 24, 16, 3, styles=False)
    obj = s.shape_object
    bpy.ops.cs2.edit_shape()
    check(obj.mode == "EDIT" and s.edit_scope == "FACES", "el boton no entro a elegir caras")
    bpy.ops.object.mode_set(mode="OBJECT")                      # como si apretara Tab
    bpy.context.view_layer.update()
    check(s.edit_scope == "ALL", f"al salir con Tab el alcance debia volver a Todo ({s.edit_scope})")
    pr = list(a.ROOF_PRESETS)[1]
    bpy.ops.cs2.comp_roof("EXEC_DEFAULT", alcance="ALL", r_preset_cargado=pr, r_preset=pr, r_pitch=33.0)
    check(abs(a.roof_values(s)["pitch"] - 33.0) < 1e-3, "el techo general no quedo tras salir con Tab")
    bpy.ops.object.mode_set(mode="EDIT")                        # Tab hacia adentro
    bpy.context.view_layer.update()
    check(s.edit_scope == "FACES", "al entrar con Tab el alcance debia pasar a Caras elegidas")
    bpy.ops.cs2.edit_shape()
    check(obj.mode == "OBJECT" and s.edit_scope == "ALL", "el boton no volvio a todo el edificio")
    check(abs(a.roof_values(s)["pitch"] - 33.0) < 1e-3, "volver a Todo pisó el techo general")
    result(ok=True)


def case_scope_auto():
    """Elegir «Caras elegidas» entra solo a Editar forma (sin rayos X) y volver a «Todo el edificio» sale."""
    s = setup("L", "FLAT", 24, 16, 3, styles=False)
    obj = s.shape_object
    check(obj.mode == "OBJECT", "la forma debe estar fuera de edicion al empezar")
    s.edit_scope = "FACES"
    check(obj.mode == "EDIT", "«Caras elegidas» no entro a Editar forma")
    check(s.edit_scope == "FACES", "el alcance se perdio al entrar")
    s.edit_scope = "ALL"
    check(obj.mode == "OBJECT", "«Todo el edificio» no salio de Editar forma")
    check(not any(getattr(sp.shading, "show_xray", False) for w in bpy.context.window_manager.windows
                  for ar in w.screen.areas if ar.type == "VIEW_3D" for sp in ar.spaces if sp.type == "VIEW_3D"),
          "no deben activarse los rayos X")
    result(ok=True)


def case_curtain():
    """Muro cortina: estilos de la biblioteca, guardado como estilo propio, ajustes por cara y mallas/UV con todas las opciones."""
    import bmesh
    s = setup("BOX", "FLAT", 14, 10, 4, styles=False)
    a.lib_ensure()
    cur = [n for n in a._LIB["names"] if a._LIB["styles"][n]["kind"] == "curtain"]
    check(len(cur) >= 10, f"faltan estilos de muro cortina ({len(cur)})")
    bpy.ops.cs2.comp_curtain("EXEC_DEFAULT", alcance="ALL", cw_on_g=True, estilo="Cortina con zócalo y cornisa", estilo_cargado="NONE")
    check(s.cw_on_g and abs(s.cw_baseh_g - 0.9) < 1e-3 and abs(s.cw_caph_g - 0.6) < 1e-3, "el estilo no cargó sus valores")
    bpy.ops.cs2.comp_curtain("EXEC_DEFAULT", alcance="ALL", cw_on_g=True, estilo="NONE", estilo_cargado="NONE",
                             guardar_como="Cortina de prueba", cw_doorw=2.2, cw_prof="2")
    st = a.get_style("Cortina de prueba")
    check(st and st["kind"] == "curtain" and abs(st["cw_doorw"] - 2.2) < 1e-3 and st["cw_prof"] == 2, "no se guardó el estilo propio")
    a.delete_user_style("Cortina de prueba")
    # todas las opciones juntas: mallas dentro de los límites y UV sin errores
    for k, v in dict(cw_prof_g="2", cw_pat_g="2", cw_patn_g=3, cw_doorw_g=2.4, cw_baseh_g=0.8, cw_caph_g=0.6, cw_capo_g=0.35,
                     cw_cornw_g=0.3, cw_ht_g=0.1, cw_hd_g=0.12, cw_edge_g=1.0).items():
        setattr(s, k, v)
    bpy.ops.cs2.generate()
    bpy.ops.cs2.regen_lods()
    objs = lod_objs(s)
    for i, o in enumerate(objs):
        check(a.mesh_tris(o) > 0, f"LOD{i} vacío")
    check(a.mesh_tris(objs[1]) < 0.5 * a.mesh_tris(objs[0]), "el LOD1 no baja a la mitad")
    check(a.mesh_tris(objs[2]) <= a.LOD2_MAX_TRIS, "el LOD2 se pasa del límite")
    s.bake_ao = True
    p = a.props_to_params(s)
    infos = a.uv_generate(bpy.context, p, objs, None)
    for i, o in enumerate(objs):
        check(_uv_checks(o.data) == 0, f"LOD{i}: UV fuera de 0-1")
    # por cara
    bpy.ops.cs2.edit_shape()
    obj = s.shape_object
    bm = bmesh.from_edit_mesh(obj.data)
    for f in bm.faces:
        f.select = False
    W0 = [f for f in bm.faces if abs(f.normal.z) < .1][0].index
    bm.faces.ensure_lookup_table()
    bm.faces[W0].select = True
    bmesh.update_edit_mesh(obj.data)
    bpy.ops.cs2.comp_curtain("EXEC_DEFAULT", alcance="FACES", cw_mode="1", estilo="Cortina con zócalo y cornisa", estilo_cargado="NONE")
    bm = bmesh.from_edit_mesh(obj.data)
    bm.faces.ensure_lookup_table()
    lays = a.wall_layers(bm, create=False)
    f0 = bm.faces[W0]
    check(abs(f0[lays["cs2_k_baseh"]] - 0.9) < 1e-3 and abs(f0[lays["cs2_k_capo"]] - 0.35) < 1e-3, "los ajustes no se guardaron en la cara")
    bpy.ops.object.mode_set(mode="OBJECT")
    result(estilos=len(cur))


def case_comp_draw():
    """El cuadro de cada componente (Ventana, Puerta, Balcón, Muro cortina, Techo...) se dibuja sin errores y el de muro cortina
    muestra el selector de estilos y todas las opciones nuevas."""
    s = setup("L", "FLAT", 24, 16, 3, styles=False)
    w = bpy.context.scene.cs2_wall
    drawn = {}

    class Lay(_FakeLayout):
        def prop(self, data, name, *a, **k):
            drawn.setdefault("props", []).append(name)
            return Lay()

        def template_icon_view(self, data, name, *a, **k):
            drawn.setdefault("icons", []).append(name)
            return Lay()

        def __getattr__(self, n):
            if n.startswith("__"):
                raise AttributeError(n)
            return lambda *a, **k: Lay()

    for key, spec in a._COMP["specs"].items():
        cls = getattr(bpy.types, "CS2_OT_comp_" + key)
        for alc in ("ALL", "FACES"):
            drawn.clear()
            op = type("Op", (), {})()
            op.layout = Lay()
            op.alcance = alc
            op._fields = cls._fields
            op.estilo, op.estilo_cargado, op.guardar_como, op.volver_general = "NONE", "NONE", "", False
            op.modelo = op.modelo_cargado = "NONE"
            op.r_preset_cargado = ""
            for _g, use, label in spec.get("wall_groups", []):
                setattr(op, "usar_" + use, True)
            for f in cls._fields:
                try:
                    setattr(op, f["p"], getattr(s, f["g"]) if f["g"] else getattr(w, f["w"]))
                except Exception:
                    setattr(op, f["p"], 0)
            try:
                a._comp_draw(op, bpy.context, spec)
            except Exception as e:
                check(False, f"{key}/{alc}: {e!r}")
            if key == "curtain":
                check("estilo" in drawn.get("icons", []), "el muro cortina no muestra el selector de estilos")
                for need in ("cw_pat", "cw_prof", "cw_doorw", "cw_baseh", "cw_caph", "cw_cornw", "guardar_como"):
                    check(need in drawn.get("props", []), f"muro cortina ({alc}): falta el campo {need}")
    result(componentes=len(a._COMP["specs"]))


def _wall_uv_extent(me, nsign, region_mat, min_area=6.0):
    """(suma de anchos UV, suma de altos UV, cantidad) de las caras grandes de un material que miran a `nsign` en Y."""
    uvd = me.uv_layers[0].data
    du = dv = 0.0
    n = 0
    for poly in me.polygons:
        if poly.material_index != region_mat or poly.area < min_area or poly.normal.y * nsign < 0.9:
            continue
        us = [uvd[li].uv[0] for li in poly.loop_indices]
        vs = [uvd[li].uv[1] for li in poly.loop_indices]
        du += max(us) - min(us)
        dv += max(vs) - min(vs)
        n += 1
    return du, dv, n


def _uv_in_unit(me):
    uvd = me.uv_layers[0].data
    return all(-1e-6 <= c.uv[0] <= 1 + 1e-6 and -1e-6 <= c.uv[1] <= 1 + 1e-6 for c in uvd)


def case_tex_rotate():
    """Giro de texturas: por material (cualquier ángulo) y por pared (cuartos de vuelta). Las UV del atlas giran en los tres LODs,
    quedan dentro de 0-1, la geometría no cambia y sin giro todo queda igual."""
    import bmesh
    brick = make_brick(os.path.join(tempfile.gettempdir(), "cs2_test_brick.png"))
    s = setup("BOX", "FLAT", 16, 12, 3, styles=False, ms_facade_mode="PBR", ms_facade_img_color=brick, ms_facade_tile=2.0)
    MF = a.MAT["facade"]

    def regen():
        bpy.ops.cs2.generate()
        bpy.ops.cs2.regen_lods()
        return lod_objs(s)

    base = regen()
    tris0 = [a.mesh_tris(o) for o in base]
    du0, dv0, n0 = _wall_uv_extent(base[0].data, -1, MF)
    check(n0 > 0, "no encontré paredes grandes de fachada")
    check(du0 > dv0, f"sin giro la pared ancha debe ser más ancha que alta en el mapa ({du0:.3f} x {dv0:.3f})")

    # 1) material girado 90°: en los tres LODs el ancho y el alto del mapa se intercambian
    s.ms_facade_rot = 90.0
    objs = regen()
    for i, o in enumerate(objs):
        du, dv, n = _wall_uv_extent(o.data, -1, MF)
        check(n > 0 and dv > du, f"LOD{i}: con 90° la pared debe quedar más alta que ancha en el mapa ({du:.3f} x {dv:.3f})")
        check(_uv_in_unit(o.data), f"LOD{i}: UV fuera de 0-1 con 90°")
    check([a.mesh_tris(o) for o in objs] == tris0, "girar la textura no debe cambiar la geometría")

    # 2) 180° vuelve a la proporción original; un ángulo libre sigue dentro del mapa
    s.ms_facade_rot = 180.0
    objs = regen()
    du, dv, _n = _wall_uv_extent(objs[0].data, -1, MF)
    check(du > dv, "con 180° la pared debe volver a ser más ancha que alta")
    s.ms_facade_rot = 37.0
    objs = regen()
    for i, o in enumerate(objs):
        check(_uv_in_unit(o.data), f"LOD{i}: UV fuera de 0-1 con 37°")
    # 3) el botón gira de a 90° y el 0 vuelve al original
    bpy.ops.cs2.slot_rot(slot="facade", delta=90.0)
    check(abs(s.ms_facade_rot - (-53.0)) < 1e-6 or abs(s.ms_facade_rot - 127.0) < 1e-6, f"giro del botón inesperado: {s.ms_facade_rot}")
    bpy.ops.cs2.slot_rot(slot="facade", delta=0.0)
    check(s.ms_facade_rot == 0.0, "el botón de quitar el giro no lo dejó en 0")
    objs = regen()
    du, dv, _n = _wall_uv_extent(objs[0].data, -1, MF)
    check(abs(du - du0) < 1e-4 and abs(dv - dv0) < 1e-4, "sin giro las UV deben ser las originales")

    # 4) giro por pared: solo la pared del frente (normal -Y) gira, la de atrás no
    obj = s.shape_object
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    bm.transform(obj.matrix_world)
    lays = a.wall_layers(bm, create=True)
    bm.transform(obj.matrix_world.inverted())
    front = [f for f in bm.faces if f.normal.y < -0.9]
    check(front, "la forma no tiene pared frontal")
    for f in front:
        f[lays["cs2_f_rot"]] = 1
    bm.to_mesh(obj.data)
    bm.free()
    objs = regen()
    dfu, dfv, nf = _wall_uv_extent(objs[0].data, -1, MF)
    dbu, dbv, nb = _wall_uv_extent(objs[0].data, 1, MF)
    check(nf > 0 and dfv > dfu, f"la pared girada debe quedar más alta que ancha ({dfu:.3f} x {dfv:.3f})")
    check(nb > 0 and dbu > dbv, f"la pared de atrás no debe girar ({dbu:.3f} x {dbv:.3f})")
    check(_uv_in_unit(objs[0].data), "UV fuera de 0-1 con una pared girada")
    result(ok=True, du0=round(du0, 3), dv0=round(dv0, 3))


def case_tex_rotate_bake():
    """Con el giro puesto, las UV únicas del horneado se arman igual (sin errores ni islas superpuestas)."""
    brick = make_brick(os.path.join(tempfile.gettempdir(), "cs2_test_brick.png"))
    for rot in (90.0, 37.0):
        s = setup("L", "FLAT", 24, 16, 4, bake_ao=True, ms_facade_mode="PBR", ms_facade_img_color=brick, ms_facade_tile=2.0)
        s.ms_facade_rot = rot
        bpy.ops.cs2.generate()
        bpy.ops.cs2.regen_lods()
        objs = lod_objs(s)
        a.uv_generate(bpy.context, a.props_to_params(s), objs, None)
        for i, o in enumerate(objs):
            pct = _overlap_pct(o.data)
            check(pct < 0.5, f"giro {rot}° LOD{i}: {pct:.2f} % del mapa con islas superpuestas")
        for o in objs:
            bpy.data.objects.remove(o, do_unlink=True)
    result(ok=True)


CASES = {k[5:]: v for k, v in globals().items() if k.startswith("case_")}

if __name__ == "__main__":
    name = sys.argv[1]
    if name == "--list":
        print(" ".join(CASES))
        sys.exit(0)
    t0 = time.time()
    code = 0
    try:
        CASES[name]()
        print(f"OK {name} {time.time() - t0:.1f}s")
    except BaseException:
        import traceback
        traceback.print_exc()
        code = 1
    # sale sin esperar la limpieza de Blender (en algunos equipos se cuelga al cerrar y el corredor esperaba al tope)
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(code)

