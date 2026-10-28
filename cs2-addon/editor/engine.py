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
# Si Blender ya tiene instalada otra versión del add-on (la de tu carpeta de add-ons), se descarta: el motor usa SIEMPRE la de esta carpeta.
old = sys.modules.get("cs2_edificio_addon")
if old is not None and os.path.abspath(getattr(old, "__file__", "") or "") != os.path.join(os.path.dirname(HERE), "cs2_edificio_addon.py"):
    try:
        old.unregister()
    except Exception:
        pass
    del sys.modules["cs2_edificio_addon"]
import cs2_edificio_addon as a  # noqa: E402

ARC_STEP = 12.0      # grados por faceta en las paredes curvas
GLOBAL_PROPS = {"window_style", "door_style", "balcony_style", "balconies", "facade_color", "string_courses",
                "mat_b_color", "mat_c_color", "mat_d_color", "trim_color", "roof_color", "door_color"}


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


def offset_poly(pts, owner, ins):
    """Planta antihoraria desplazada hacia adentro: cada faceta `i` se corre `ins[owner[i]]` metros y las vecinas se unen en la esquina."""
    n = len(pts)
    lines = []
    for i in range(n):
        p, q = pts[i], pts[(i + 1) % n]
        L = math.hypot(q[0] - p[0], q[1] - p[1]) or 1e-9
        d = float(ins[owner[i]] or 0.0)
        dx, dy = (q[0] - p[0]) / L, (q[1] - p[1]) / L
        lines.append(((p[0] - dy * d, p[1] + dx * d), (dx, dy)))
    out = []
    for i in range(n):
        (p0, d0), (p1, d1) = lines[i - 1], lines[i]
        cr = d0[0] * d1[1] - d0[1] * d1[0]
        if abs(cr) < 1e-6:
            out.append(p1)
            continue
        t = ((p1[0] - p0[0]) * d1[1] - (p1[1] - p0[1]) * d1[0]) / cr
        out.append((p0[0] + d0[0] * t, p0[1] + d0[1] * t))
    for i in range(n):          # la planta no puede darse vuelta ni cruzarse
        a0, a1, b0, b1 = pts[i], pts[(i + 1) % n], out[i], out[(i + 1) % n]
        if (a1[0] - a0[0]) * (b1[0] - b0[0]) + (a1[1] - a0[1]) * (b1[1] - b0[1]) <= 0:
            raise RuntimeError("El retranqueo es demasiado grande para esa pared")
    if area(out) <= 0.5:
        raise RuntimeError("El retranqueo deja el piso sin superficie")
    return out


def make_levels(pts, owner, facets_of, nedges, floors, fh, setbacks):
    """Tramos de altura con su planta: [{z0, z1, P, facets}]. Los retranqueos solo pueden crecer hacia arriba (por pared)."""
    sbs = sorted([sb for sb in (setbacks or []) if 1 <= int(sb.get("from", 0)) < floors], key=lambda sb: int(sb["from"]))
    zs = [0.0] + [int(sb["from"]) * fh for sb in sbs] + [floors * fh]
    cum = [0.0] * nedges
    levels = [dict(z0=zs[0], z1=zs[1], P=list(pts), ins=list(cum))]
    for k, sb in enumerate(sbs):
        ins = list(sb.get("ins") or [])
        ins += [0.0] * (nedges - len(ins))
        cum = [max(cum[i], float(ins[i] or 0.0)) for i in range(nedges)]
        levels.append(dict(z0=zs[k + 1], z1=zs[k + 2], P=offset_poly(pts, owner, cum), ins=list(cum)))
    for lv in levels:
        n = len(lv["P"])
        fs = [[] for _ in range(nedges)]
        for i in range(n):
            fs[owner[i]].append((lv["P"][i], lv["P"][(i + 1) % n]))
        lv["facets"] = fs
    return levels


def stepped_shape(ctx, levels, floors, fh):
    """Malla cerrada con una planta por tramo (paredes, terrazas entre tramos y tapas)."""
    bm = bmesh.new()
    cache = {}

    def V(p, z):
        k = (round(p[0], 4), round(p[1], 4), round(z, 4))
        if k not in cache:
            cache[k] = bm.verts.new((p[0], p[1], z))
        return cache[k]

    def face(vs):
        u = []
        for v in vs:
            if not u or u[-1] is not v:
                u.append(v)
        if len(u) > 1 and u[0] is u[-1]:
            u.pop()
        if len(set(u)) < 3:
            return
        try:
            bm.faces.new(u)
        except ValueError:
            pass
    P0 = levels[0]["P"]
    face([V(p, 0.0) for p in reversed(P0)])
    for k, lv in enumerate(levels):
        P, n = lv["P"], len(lv["P"])
        for i in range(n):
            a_, b_ = P[i], P[(i + 1) % n]
            face([V(a_, lv["z0"]), V(b_, lv["z0"]), V(b_, lv["z1"]), V(a_, lv["z1"])])
        if k + 1 < len(levels):
            Q = levels[k + 1]["P"]
            for i in range(n):
                j = (i + 1) % n
                face([V(P[i], lv["z1"]), V(P[j], lv["z1"]), V(Q[j], lv["z1"]), V(Q[i], lv["z1"])])
    face([V(p, floors * fh) for p in levels[-1]["P"]])
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    me = bpy.data.meshes.new("CS2_Forma")
    bm.to_mesh(me)
    bm.free()
    obj = bpy.data.objects.new("CS2_Forma", me)
    ctx.scene.collection.objects.link(obj)
    return obj


def level_of(levels, z):
    return next((k for k, lv in enumerate(levels) if z <= lv["z1"] + 1e-3), len(levels) - 1)


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
    levels = make_levels(pts, owner, facets, len(verts), floors, fh, rec.get("setbacks"))
    if len(levels) == 1:
        obj = a.create_shape_object(ctx, "BOX", 1, 1, floors * fh, 0.0, pts=pts)
    else:
        obj = stepped_shape(ctx, levels, floors, fh)
    # ajustes por pared (atributos de cara)
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    lays = a.wall_layers(bm, create=True)
    for f in bm.faces:
        if abs(f.normal.z) > 0.12:
            continue
        c = f.calc_center_median()
        P = levels[level_of(levels, c.z)]["P"]
        i = min(range(len(P)), key=lambda j: _dist_seg((c.x, c.y), (P[j], P[(j + 1) % len(P)])))
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
    return s, obj, dict(verts=verts, edges=edges, pts=pts, owner=owner, facets=facets, floors=floors, fh=fh, levels=levels)


def wall_index(shape, info):
    """Para cada (borde de la planta, tramo de altura), el índice de una pared de `shape` que le corresponde."""
    c = shape["center"]
    found = {}
    lv = info["levels"]
    for idx, (wp, n, cfg) in enumerate(shape["walls"]):
        m = sum((q for q in wp), Vector((0, 0, 0))) / len(wp) + c
        zmin = min(q.z for q in wp) + c.z
        k = min(range(len(lv)), key=lambda t: abs(lv[t]["z0"] - zmin))
        P = lv[k]["P"]
        i = min(range(len(P)), key=lambda j: _dist_seg((m.x, m.y), (P[j], P[(j + 1) % len(P)])))
        found.setdefault((info["owner"][i], k), idx)
    return found


def build(rec):
    s, obj, info = prepare(rec)
    shape = a.load_shape_for(s)
    p = a.props_to_params(s)
    p["width"], p["depth"] = shape["size"].x, shape["size"].y
    widx = wall_index(shape, info)
    data = {"walls": []}
    layouts = {}
    def edits_of(ei, sec):
        e = info["edges"][ei]
        return e if sec == 0 else ((e.get("sx") or {}).get(str(sec)) or {})
    for (ei, sec), idx in widx.items():
        lay = a.slot_layout(p, shape, idx)
        layouts[(ei, sec)] = lay
        e = edits_of(ei, sec)
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
    for (ei, sec), lay in sorted(layouts.items(), key=lambda kv: (kv[0][1], kv[0][0])):
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
        ch = (shape["walls"][widx[(ei, sec)]][2] or {}).get("_chain")
        if ch:   # en una curva, puertas y balcones solo existen en las facetas que miran al frente (o si se piden en todas)
            cfg = shape["walls"][widx[(ei, sec)]][2]
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
        walls.append(dict(edge=ei, sec=sec, key=lay["key"], amin=lay["amin"], amax=lay["amax"], zmin=lay["zmin"], zmax=lay["zmax"], fh=d["fh"], nfl=lay["nfl"],
                          defaults=d, slots=slots, facets=[[list(q0), list(q1)] for q0, q1 in info["levels"][sec]["facets"][ei]]))
    out["walls"] = walls
    out["levels"] = [dict(z0=lv["z0"], z1=lv["z1"], poly=[list(q) for q in lv["P"]]) for lv in info["levels"]]
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


class Srv(http.server.ThreadingHTTPServer):
    allow_reuse_address = False      # en Windows, con True otra instancia podía quedarse con las conexiones y el navegador veía una respuesta vacía
    daemon_threads = True

    def handle_error(self, request, client_address):
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    port0 = int(sys.argv[sys.argv.index("--port") + 1]) if "--port" in sys.argv else 8765
    srv = None
    for port in range(port0, port0 + 20):
        try:
            srv = Srv(("127.0.0.1", port), H)
            break
        except OSError:
            print(f"El puerto {port} está ocupado (¿hay otro motor abierto?), pruebo el siguiente…", flush=True)
    if srv is None:
        raise SystemExit("No encontré un puerto libre. Cerrá las otras ventanas del motor y probá de nuevo.")
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
