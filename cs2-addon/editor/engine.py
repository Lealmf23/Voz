"""Motor del editor: servidor local que usa Blender (módulo bpy) para armar la malla real del edificio.
Uso:  python3 engine.py [--port 8765]   (requiere `pip install bpy`; con Blender: blender -b -P engine.py)
Endpoints:  GET /  -> editor.html     POST /build  {receta}  -> {tris, mats, v, f, m}
"""
import json
import os
import sys
import http.server

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import bpy  # noqa: E402
import cs2_edificio_addon as a  # noqa: E402


def build(rec):
    """Receta -> malla principal (LOD0). Receta: {pts:[[x,y]..], floors, floor_h, ...ajustes del addon}."""
    if not hasattr(bpy.types.Scene, "cs2_building"):
        a.register()
    ctx = bpy.context
    s = ctx.scene.cs2_building
    for o in list(bpy.data.objects):
        bpy.data.objects.remove(o, do_unlink=True)
    pts = [tuple(p) for p in rec["pts"]]
    if a_area(pts) < 0:
        pts.reverse()
    floors = int(rec.get("floors", 3))
    fh = float(rec.get("floor_h", 3.0))
    s.live_update = False
    s.floors = floors
    s.floor_height = fh
    for k, v in (rec.get("props") or {}).items():
        setattr(s, k, v)
    obj = a.create_shape_object(ctx, "BOX", 1, 1, floors * fh, fh, pts=pts)
    s.shape_object = obj
    s.mode = "shape"
    s.live_update = True
    bpy.ops.cs2.generate()
    main = a._main_obj(s)
    me = main.data
    me.calc_loop_triangles()
    v = [round(c, 4) for vt in me.vertices for c in vt.co]
    f, m = [], []
    for t in me.loop_triangles:
        f.extend(t.vertices)
        m.append(t.material_index)
    mats = [sl.material.name.split("_", 1)[-1] if sl.material else "" for sl in main.material_slots]
    cols = {}
    for sl in main.material_slots:
        if sl.material:
            cols[sl.material.name.split("_", 1)[-1]] = [round(c, 3) for c in sl.material.diffuse_color[:3]]
    return {"tris": len(me.loop_triangles), "v": v, "f": f, "m": m, "mats": mats, "cols": cols}


def a_area(pts):
    return sum(pts[i][0] * pts[(i + 1) % len(pts)][1] - pts[(i + 1) % len(pts)][0] * pts[i][1] for i in range(len(pts))) / 2


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
        if p == "/":
            p = "/editor.html"
        f = os.path.normpath(os.path.join(HERE, p.lstrip("/")))
        if not f.startswith(HERE) or not os.path.isfile(f):
            return self._send(404, "no existe", "text/plain")
        ct = "text/html" if f.endswith(".html") else "text/javascript" if f.endswith(".js") else "application/octet-stream"
        self._send(200, open(f, "rb").read(), ct + "; charset=utf-8")

    def do_POST(self):
        if self.path != "/build":
            return self._send(404, "{}")
        try:
            rec = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
            self._send(200, json.dumps(build(rec)))
        except Exception as e:  # el editor muestra el mensaje
            import traceback
            traceback.print_exc()
            self._send(500, json.dumps({"error": str(e)}))

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    port = int(sys.argv[sys.argv.index("--port") + 1]) if "--port" in sys.argv else 8765
    print(f"Motor listo en http://127.0.0.1:{port}  (Ctrl+C para cerrar)", flush=True)
    http.server.ThreadingHTTPServer(("127.0.0.1", port), H).serve_forever()
