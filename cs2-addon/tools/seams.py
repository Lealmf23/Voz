"""Mide los saltos de sombra en los bordes entre paños de pared vecinos (coplanares, mismo material) en el mapa horneado del LOD0.
Uso: python3 tools/seams.py [--sin-compartir] [--sin-tramos] [--area M2] [--tol T] [--rayos N]
Imprime ERROR: diferencia entre la sombra horneada y la exacta (64 rayos) en puntos al azar de las paredes."""
import math
import os
import sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tests"))
from common import a, bpy, setup, lod_objs  # noqa: E402
import numpy as np  # noqa: E402


def seam_stats(me, ao, inset_px=2.0):
    size = ao.shape[0]
    uvl = me.uv_layers["CS2_unique"].data
    walls = {a.MAT["facade"], a.MAT["facb"], a.MAT["facc"], a.MAT["facd"]}
    e2f = {}
    for poly in me.polygons:
        if poly.material_index not in walls or abs(poly.normal.z) > 0.3:
            continue
        for k in poly.edge_keys:
            e2f.setdefault(k, []).append(poly.index)

    def uv_at(poly, P):
        # baricéntricas en el triángulo (abanico) de la cara que contiene P
        vs = [me.vertices[me.loops[li].vertex_index].co for li in poly.loop_indices]
        uvs = [uvl[li].uv for li in poly.loop_indices]
        for i in range(1, len(vs) - 1):
            A, B, C = vs[0], vs[i], vs[i + 1]
            v0, v1, v2 = B - A, C - A, P - A
            d00, d01, d11, d20, d21 = v0.dot(v0), v0.dot(v1), v1.dot(v1), v2.dot(v0), v2.dot(v1)
            den = d00 * d11 - d01 * d01
            if abs(den) < 1e-12:
                continue
            v = (d11 * d20 - d01 * d21) / den
            w = (d00 * d21 - d01 * d20) / den
            u = 1 - v - w
            if min(u, v, w) >= -1e-4:
                return (uvs[0] * u + uvs[i] * v + uvs[i + 1] * w)
        return None

    def sample(uv):
        x, y = uv[0] * size - 0.5, uv[1] * size - 0.5
        x0, y0 = int(math.floor(x)), int(math.floor(y))
        fx, fy = x - x0, y - y0
        x0, y0 = max(0, min(size - 2, x0)), max(0, min(size - 2, y0))
        return (ao[y0, x0] * (1 - fx) * (1 - fy) + ao[y0, x0 + 1] * fx * (1 - fy) + ao[y0 + 1, x0] * (1 - fx) * fy + ao[y0 + 1, x0 + 1] * fx * fy)

    difs = []
    for (i, j), fs in e2f.items():
        if len(fs) != 2:
            continue
        pa, pb = me.polygons[fs[0]], me.polygons[fs[1]]
        if pa.normal.dot(pb.normal) < 0.999:
            continue
        A, B = me.vertices[i].co, me.vertices[j].co
        if (B - A).length < 0.2:
            continue
        for t in (0.2, 0.4, 0.6, 0.8):
            P = A.lerp(B, t)
            ia = (pa.center - P).normalized()
            ib = (pb.center - P).normalized()
            # se entra 3 cm en cada cara (a 1K y ~25 px/m son ~1 texel)
            ua, ub = uv_at(pa, P + ia * 0.03), uv_at(pb, P + ib * 0.03)
            if ua is None or ub is None:
                continue
            difs.append(abs(sample(ua) - sample(ub)))
    d = np.array(difs) if difs else np.zeros(1)
    return {"bordes": len(difs), "media": round(float(d.mean()), 4), "p90": round(float(np.percentile(d, 90)), 4),
            "max": round(float(d.max()), 3), "saltos>0.05": int((d > 0.05).sum())}


def bake_error(me, ao, n_per_face=6, rays=64, dist=2.5):
    """Error de la sombra horneada contra la exacta (64 rayos) en puntos al azar de las paredes del LOD0."""
    import random
    from mathutils import Vector
    rnd = random.Random(1)
    size = ao.shape[0]
    uvl = me.uv_layers["CS2_unique"].data
    bvh = a._occluder_bvh(me)
    dirs = a._hammersley_dirs(rays)
    walls = {a.MAT["facade"], a.MAT["facb"], a.MAT["facc"], a.MAT["facd"]}
    me.calc_loop_triangles()
    errs = []
    for t in me.loop_triangles:
        poly = me.polygons[t.polygon_index]
        if poly.material_index not in walls or t.area < 0.02:
            continue
        vs = [me.vertices[i].co for i in t.vertices]
        uvs = [uvl[li].uv for li in t.loops]
        for _ in range(n_per_face):
            r1, r2 = rnd.random(), rnd.random()
            if r1 + r2 > 1:
                r1, r2 = 1 - r1, 1 - r2
            w = (1 - r1 - r2, r1, r2)
            P = vs[0] * w[0] + vs[1] * w[1] + vs[2] * w[2]
            uv = uvs[0] * w[0] + uvs[1] * w[1] + uvs[2] * w[2]
            x, y = min(size - 1, int(uv[0] * size)), min(size - 1, int(uv[1] * size))
            ex = sum(a._ao_at(bvh, dirs, P, poly.normal, dist, r_) for r_ in (0.0, 1.1, 2.3)) / 3.0
            errs.append(abs(float(ao[y, x]) - ex))
    e = np.array(errs)
    return {"puntos": len(e), "error_medio": round(float(e.mean()), 4), "p90": round(float(np.percentile(e, 90)), 3),
            "p99": round(float(np.percentile(e, 99)), 3), ">0.1": round(float((e > 0.1).mean() * 100), 1)}


if __name__ == "__main__":
    kw = dict(bake_ao=True, balconies="alternate", balcony_style="Balcón baranda maciza", string_courses=True)
    if "--sin-compartir" in sys.argv:
        kw["bake_share"] = False
    if "--sin-tramos" in sys.argv:
        os.environ["CS2_NO_CROP"] = "1"
    if "--area" in sys.argv:
        kw["bake_share_area"] = float(sys.argv[sys.argv.index("--area") + 1])
    if "--tol" in sys.argv:
        kw["bake_share_loose"] = float(sys.argv[sys.argv.index("--tol") + 1])
    if "--rayos" in sys.argv:
        kw["bake_samples"] = int(sys.argv[sys.argv.index("--rayos") + 1])
    s = setup("BOX", "FLAT", 20, 12, 3, **kw)
    objs = lod_objs(s)
    p = a.props_to_params(s)
    if os.environ.get("CS2_NO_CROP"):
        p["bake_crop"] = False
    info = a.uv_generate(bpy.context, p, objs, None)
    import time
    t0 = time.time()
    a.bake_ao_all(bpy.context, p, objs, None)
    print("TIEMPO_AO", round(time.time() - t0, 2), flush=True)
    me = objs[0].data
    ao = a.read_ao(p["building_name"], 0, a.uv_state(me)["uid"])
    print("ERROR", bake_error(me, ao), {k: info[0].get(k) for k in ("px_por_m", "islas", "uso")}, flush=True)
    sys.stdout.flush()
    os._exit(0)
