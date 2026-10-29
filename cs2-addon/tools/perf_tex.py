"""Mide el armado de UV, el horneado de sombras y la aplicación de texturas (tiempos y calidad) en varios edificios.
Uso: python3 tools/perf_tex.py [--prof] [salida.json]"""
import cProfile
import io
import json
import os
import pstats
import sys
import time
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tests"))
from common import a, bpy, setup, lod_objs, make_brick  # noqa: E402

CASES = [("L5", ("L", "FLAT", 24, 16, 5), {}),] if os.environ.get("PERF_ONE") else [("L5", ("L", "FLAT", 24, 16, 5), {}),
         ("BOX8_gable", ("BOX", "GABLE_TILE", 20, 14, 8), {}),
         ("U6_bal", ("U", "FLAT", 30, 24, 6), dict(balconies="all")),
         ]


def run(prof=False):
    brick = make_brick(os.path.join("/tmp", "cs2_perf_brick.png"))
    out = {}
    for name, args, kw in CASES:
        s = setup(*args, bake_ao=True, ms_facade_mode="PBR", ms_facade_img_color=brick, ms_facade_tile=2.0, **kw)
        objs = lod_objs(s)
        p = a.props_to_params(s)
        r = {"tris": [a.mesh_tris(o) for o in objs]}
        pr = cProfile.Profile() if prof else None
        t = time.time()
        if pr: pr.enable()
        infos = a.uv_generate(bpy.context, p, objs, None)
        if pr: pr.disable()
        r["t_uv"] = round(time.time() - t, 2)
        r["uv"] = [{k: i.get(k) for k in ("px_por_m", "uso", "islas", "compartidas", "caras")} for i in infos]
        t = time.time()
        if pr: pr.enable()
        a.bake_ao_all(bpy.context, p, objs, None)
        if pr: pr.disable()
        r["t_ao"] = round(time.time() - t, 2)
        t = time.time()
        if pr: pr.enable()
        a.assign_textures(bpy.context, p, objs, None)
        if pr: pr.disable()
        r["t_tex"] = round(time.time() - t, 2)
        out[name] = r
        print(name, json.dumps(r), flush=True)
        if pr:
            st = io.StringIO()
            pstats.Stats(pr, stream=st).sort_stats("cumulative").print_stats(28)
            print(st.getvalue()[:6000], flush=True)
        for o in list(bpy.data.objects):
            bpy.data.objects.remove(o, do_unlink=True)
    return out


if __name__ == "__main__":
    prof = "--prof" in sys.argv
    args = [x for x in sys.argv[1:] if not x.startswith("--")]
    res = run(prof)
    if args:
        json.dump(res, open(args[0], "w"), indent=1)
    sys.stdout.flush()
    os._exit(0)
