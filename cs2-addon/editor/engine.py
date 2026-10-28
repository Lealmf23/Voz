"""Motor del editor: servidor local que usa Blender (módulo bpy) con el add-on para armar la malla real del edificio.
Uso:  python3 engine.py [--port 8765]   (requiere `pip install bpy`; con Blender: blender -b -P engine.py)
Endpoints:  GET /            editor.html y archivos estáticos
            GET /catalog     estilos de ventanas, puertas y balcones, techos y materiales
            POST /build      receta -> malla y reparto de aberturas por pared
            POST /export     receta -> LODs + FBX/texturas en la carpeta pedida
Receta: {verts:[[x,y]..], floors, floor_h, roof, props:{}, edges:[{bulge, mat, rot, ws, ds, bs, ov:{}, add:[]}..]}
"""
import json
import math
import os
import sys
import http.server
import queue
import threading

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import bpy  # noqa: E402
import bmesh  # noqa: E402
from mathutils import Vector  # noqa: E402
import cs2_edificio_addon as a  # noqa: E402

ARC_STEP = 12.0      # grados por faceta en las paredes curvas
GLOBAL_PROPS = {"window_style", "door_style", "balcony_style", "balconies", "facade_color", "string_courses"}


def area(pts):
    return sum(pts[i][0] * pts[(i + 1) % len(pts)][1] - pts[(i + 1) % len(pts)][0] * pts[i][1] for i in range(len(pts))) / 2


def expand(verts, edges):
    """Planta con las curvas hechas facetas. Devuelve (puntos, borde de cada faceta, [facetas por borde])."""
    n = len(verts)
    pts, owner, facets = [], [], []
    for i in range(n):
        p0, p1 = verts[i], verts[(i + 1) % n]
        bulge = float((edges[i] if i < len(edges) else {}).get("bulge", 0.0) or 0.0)
        seg = [tuple(p0)]
        c = math.hypot(p1[0] - p0[0], p1[1] - p0[1])
        if abs(bulge) > 0.05 and c > 0.5:
            h = abs(bulge)
            R = (c * c / 4 + h * h) / (2 * h)
            ang = 4 * math.atan2(h, c / 2)          # ángulo total del arco
            k = max(2, int(math.ceil(math.degrees(ang) / ARC_STEP)))
            mx, my = (p0[0] + p1[0]) / 2, (p0[1] + p1[1]) / 2
            ox, oy = (p1[1] - p0[1]) / c, -(p1[0] - p0[0]) / c        # hacia afuera (planta antihoraria)
            sg = 1 if bulge > 0 else -1
            d = R - h
            cx, cy = mx - ox * sg * d, my - oy * sg * d
            a0 = math.atan2(p0[1] - cy, p0[0] - cx)
            a1 = math.atan2(p1[1] - cy, p1[0] - cx)
            pm = (mx + ox * sg * h, my + oy * sg * h)
            am = math.atan2(pm[1] - cy, pm[0] - cx)
            ccw = ((am - a0) % (2 * math.pi)) < ((a1 - a0) % (2 * math.pi))
            span = ((a1 - a0) % (2 * math.pi)) if ccw else -((a0 - a1) % (2 * math.pi))
            for j in range(1, k):
                t = a0 + span * j / k
                seg.append((cx + R * math.cos(t), cy + R * math.sin(t)))
        seg.append(tuple(p1))
        fs = []
        for j in range(len(seg) - 1):
            fs.append((seg[j], seg[j + 1]))
            pts.append(seg[j])
            owner.append(i)
        facets.append(fs)
    return pts, owner, facets


def _dist_seg(p, s):
    (x0, y0), (x1, y1) = s
    dx, dy = x1 - x0, y1 - y0
    L2 = dx * dx + dy * dy or 1e-9
    t = max(0.0, min(1.0, ((p[0] - x0) * dx + (p[1] - y0) * dy) / L2))
    return math.hypot(p[0] - (x0 + t * dx), p[1] - (y0 + t * dy))


def _clean():
    for o in list(bpy.data.objects):
        bpy.data.objects.remove(o, do_unlink=True)
    for c in list(bpy.data.collections):
        bpy.data.collections.remove(c)


def prepare(rec):
    """Arma la forma con los ajustes por pared y las ediciones de ranuras. Devuelve (s, obj, info) sin generar el edificio."""
    if not hasattr(bpy.types.Scene, "cs2_building"):
        a.register()
    ctx = bpy.context
    s = ctx.scene.cs2_building
    _clean()
    s.live_update = False
    verts = [tuple(v) for v in rec["verts"]]
    edges = list(rec.get("edges") or [])
    edges += [{}] * (len(verts) - len(edges))
    if area(verts) < 0:                      # la planta tiene que ser antihoraria: se invierten también los bordes
        verts.reverse()
        edges = [dict(e) for e in reversed(edges)]
        edges = edges[1:] + edges[:1]
    floors, fh = int(rec.get("floors", 3)), float(rec.get("floor_h", 3.0))
    s.building_name = rec.get("name") or "Edificio01"
    s.floors, s.floor_height = floors, fh
    for k, v in (rec.get("props") or {}).items():
        if k in GLOBAL_PROPS:
            setattr(s, k, v)
    if rec.get("roof"):
        s.r_preset = rec["roof"]
    pts, owner, facets = expand(verts, edges)
    obj = a.create_shape_object(ctx, "BOX", 1, 1, floors * fh, 0.0, pts=pts)
    # ajustes por pared (atributos de cara)
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    lays = a.wall_layers(bm, create=True)
    for f in bm.faces:
        if abs(f.normal.z) > 0.12:
            continue
        c = f.calc_center_median()
        i = min(range(len(pts)), key=lambda j: _dist_seg((c.x, c.y), (pts[j], pts[(j + 1) % len(pts)])))
        e = edges[owner[i]]
        if e.get("mat"):
            f[lays["cs2_f_mat"]] = int(e["mat"])
        if e.get("rot"):
            f[lays["cs2_f_rot"]] = int(e["rot"])
        for key, attr in (("ws", "cs2_w_style"), ("ds", "cs2_d_style"), ("bs", "cs2_b_style")):
            if e.get(key):
                f[lays[attr]] = a.STYLE_ID_NONE if e[key] == "__NONE__" else a.style_id(e[key])
    bm.to_mesh(obj.data)
    bm.free()
    s.shape_object = obj
    s.mode = "shape"
    return s, obj, dict(verts=verts, edges=edges, pts=pts, owner=owner, facets=facets, floors=floors, fh=fh)


def wall_index(shape, info):
    """Para cada borde de la planta, el índice de una pared de `shape` que le corresponde."""
    c = shape["center"]
    found = {}
    pts = info["pts"]
    for idx, (wp, n, cfg) in enumerate(shape["walls"]):
        m = sum((q for q in wp), Vector((0, 0, 0))) / len(wp) + c
        i = min(range(len(pts)), key=lambda j: _dist_seg((m.x, m.y), (pts[j], pts[(j + 1) % len(pts)])))
        found.setdefault(info["owner"][i], idx)
    return found


def build(rec):
    s, obj, info = prepare(rec)
    shape = a.load_shape_for(s)
    p = a.props_to_params(s)
    p["width"], p["depth"] = shape["size"].x, shape["size"].y
    widx = wall_index(shape, info)
    data = {"walls": []}
    layouts = {}
    for ei, idx in widx.items():
        lay = a.slot_layout(p, shape, idx)
        layouts[ei] = lay
        e = info["edges"][ei]
        if e.get("ov") or e.get("add"):
            data["walls"].append({"key": list(lay["key"]), "ov": dict(e.get("ov") or {}), "add": list(e.get("add") or [])})
    a.slot_edits_save(obj, data)
    bpy.ops.cs2.generate()
    s = bpy.context.scene.cs2_building
    main = a._main_obj(s)
    me = main.data
    me.calc_loop_triangles()
    out = {"tris": len(me.loop_triangles), "center": [shape["center"].x, shape["center"].y]}
    out["v"] = [round(c, 3) for vt in me.vertices for c in vt.co]
    f, m = [], []
    for t in me.loop_triangles:
        f.extend(t.vertices)
        m.append(t.material_index)
    out["f"], out["m"] = f, m
    out["mats"] = [sl.material.name.split("_", 1)[-1] if sl.material else "" for sl in main.material_slots]
    out["cols"] = {sl.material.name.split("_", 1)[-1]: [round(c, 3) for c in sl.material.diffuse_color[:3]] for sl in main.material_slots if sl.material}
    edata = a.slot_edits_load(obj)
    walls = []
    for ei in range(len(info["verts"])):
        lay = layouts.get(ei)
        if lay is None:
            walls.append(None)
            continue
        ent = a.slot_edits_match(edata, lay["key"])
        d = lay["defaults"]
        slots = []
        for sl in lay["slots"]:
            ef = a.slot_effective(lay, ent, (sl["f"], sl["k"]))
            ov = ((ent or {}).get("ov") or {}).get(f"{sl['f']},{sl['k']}", {})
            if ef is None:
                continue
            slots.append(dict(id=f"{sl['f']},{sl['k']}", kind=ef["kind"], f=ef["f"], a0=ef["a0"], a1=ef["a1"],
                              z0=lay["zmin"] + ef["f"] * d["fh"] + ef["sill"], z1=lay["zmin"] + ef["f"] * d["fh"] + ef["sill"] + ef["h"],
                              style=ef["style"], edited=ef["edited"], deleted=bool(ov.get("del")) or ef["kind"] == "none"))
        for i, ad in enumerate(((ent or {}).get("add")) or []):
            ef = a.slot_effective(lay, ent, ("add", i))
            if ef is None:
                continue
            zb = lay["zmin"] + ef["f"] * d["fh"]
            slots.append(dict(id=f"add,{i}", kind=ef["kind"], f=ef["f"], a0=ef["a0"], a1=ef["a1"], z0=zb + ef["sill"], z1=zb + ef["sill"] + ef["h"],
                              style=ef["style"], edited=True, deleted=False))
        ch = (shape["walls"][widx[ei]][2] or {}).get("_chain")
        if ch:   # en una curva, puertas y balcones solo existen en las facetas que miran al frente (o si se piden en todas)
            cfg = shape["walls"][widx[ei]][2]
            for sl_ in slots:
                if sl_["kind"] in ("door", "bal") and not sl_["id"].startswith("add,"):
                    c_, acc = (sl_["a0"] + sl_["a1"]) / 2, 0.0
                    fac = ch["facets"][-1]
                    for f_ in ch["facets"]:
                        acc += math.hypot(f_["E"][0] - f_["S"][0], f_["E"][1] - f_["S"][1])
                        if c_ <= acc:
                            fac = f_
                            break
                    if not a._chain_keep_bal(p, cfg, fac["n"]):
                        sl_["deleted"] = True
        walls.append(dict(key=lay["key"], amin=lay["amin"], amax=lay["amax"], zmin=lay["zmin"], zmax=lay["zmax"], fh=d["fh"], nfl=lay["nfl"],
                          defaults=d, slots=slots, facets=[[list(q0), list(q1)] for q0, q1 in info["facets"][ei]]))
    out["walls"] = walls
    return out


def catalog():
    a.lib_ensure()
    cat = {"window": [], "door": [], "balcony": []}
    for n in a._LIB["names"]:
        k = a._LIB["styles"][n].get("kind")
        if k in cat:
            cat[k].append({"name": n, "thumb": "thumbs/" + slug(n) + ".png", "user": n in a._LIB["user"]})
    cat["roofs"] = [{"id": k, "label": v[0]} for k, v in a.ROOF_PRESETS.items()]
    cat["mats"] = [{"id": i, "label": l} for i, l in ((0, "Igual al edificio"), (1, "A · fachada"), (2, "B · ladrillo"), (3, "C · gris oscuro"), (4, "D · madera"))]
    return cat


def export(rec):
    """Arma el edificio y exporta LODs, UV y FBX con el add-on a la carpeta pedida."""
    out = os.path.expanduser(rec.get("out") or "").strip()
    if not out:
        raise RuntimeError("Falta elegir la carpeta de salida")
    os.makedirs(out, exist_ok=True)
    build(rec)
    s = bpy.context.scene.cs2_building
    s.export_dir = out
    res = bpy.ops.cs2.export()
    if "FINISHED" not in res:
        raise RuntimeError("El add-on no pudo exportar (mirá la consola del motor)")
    objs = a._lod_objs(s)
    warn = [t for t, ok in a.lod_report(objs) if not ok] if objs else []
    return {"tris": [a.mesh_tris(o) for o in objs] if objs else [], "warn": warn, "path": os.path.abspath(out)}


def slug(name):
    import re
    return re.sub(r"[^A-Za-z0-9_-]+", "_", name)


JOBS = queue.Queue()


def run_in_main(fn, rec):
    """Blender (bpy) solo es confiable en el hilo principal: los pedidos HTTP esperan su turno en una cola."""
    done = threading.Event()
    box = {}
    JOBS.put((fn, rec, box, done))
    done.wait()
    if "err" in box:
        raise box["err"]
    return box["ok"]


class H(http.server.BaseHTTPRequestHandler):
    def _send(self, code, body, ctype="application/json"):
        b = body if isinstance(body, bytes) else body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(b)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(b)

    def do_OPTIONS(self):
        self.send_response(204)
        for k, v in (("Access-Control-Allow-Origin", "*"), ("Access-Control-Allow-Headers", "*"), ("Access-Control-Allow-Methods", "GET,POST")):
            self.send_header(k, v)
        self.end_headers()

    def do_GET(self):
        p = self.path.split("?")[0]
        if p == "/catalog":
            return self._send(200, json.dumps(run_in_main(lambda _r: catalog(), None)))
        if p == "/":
            p = "/editor.html"
        f = os.path.normpath(os.path.join(HERE, p.lstrip("/")))
        if not f.startswith(HERE) or not os.path.isfile(f):
            return self._send(404, "no existe", "text/plain")
        ct = {"html": "text/html", "js": "text/javascript", "png": "image/png", "json": "application/json"}.get(f.rsplit(".", 1)[-1], "application/octet-stream")
        self._send(200, open(f, "rb").read(), ct + ("; charset=utf-8" if ct.startswith("text") else ""))

    def do_POST(self):
        fn = {"/build": build, "/export": export}.get(self.path)
        if fn is None:
            return self._send(404, "{}")
        try:
            rec = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
            self._send(200, json.dumps(run_in_main(fn, rec)))
        except Exception as e:  # el editor muestra el mensaje
            self._send(500, json.dumps({"error": str(e)}))

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    port = int(sys.argv[sys.argv.index("--port") + 1]) if "--port" in sys.argv else 8765
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", port), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    print(f"Motor listo en http://127.0.0.1:{port}  (Ctrl+C para cerrar)", flush=True)
    if "--open" in sys.argv:
        import webbrowser
        webbrowser.open(f"http://127.0.0.1:{port}/")
    try:
        while True:
            fn, rec, box, done = JOBS.get()
            try:
                box["ok"] = fn(rec)
            except Exception as e:
                import traceback
                traceback.print_exc()
                box["err"] = e
            done.set()
    except KeyboardInterrupt:
        pass
