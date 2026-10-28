"""Comprueba que cada ventana del reparto que devuelve el motor cae sobre vidrio real de la malla (también con retranqueos).
Uso: python3 tests/overlay_check.py   (necesita el módulo bpy)"""
import math
import os
import sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import engine as e


def check(rec, label):
    r = e.build(rec)
    cx, cy = r["center"]
    V, F, M = r["v"], r["f"], r["m"]
    gi = [i for i, n in enumerate(r["mats"]) if n == "glass"]
    cents = []
    for t, m in enumerate(M):
        if m in gi:
            ids = F[t * 3:t * 3 + 3]
            cents.append(tuple(sum(V[i * 3 + k] for i in ids) / 3 for k in range(3)))
    bad = tot = 0
    for w in r["walls"]:
        path, c = [], w["amin"]            # el reparto mide desde `amin` (puede ser negativo)
        for p, q in w["facets"]:
            L = math.hypot(q[0] - p[0], q[1] - p[1])
            path.append((p, (q[0] - p[0]) / L, (q[1] - p[1]) / L, L, c))
            c += L
        for s in w["slots"]:
            if s["deleted"] or s["kind"] != "win":
                continue
            sm = (s["a0"] + s["a1"]) / 2
            f = next((f for f in path if sm <= f[4] + f[3] + 1e-9), path[-1])
            t = sm - f[4]
            x, y, z = f[0][0] + f[1] * t - cx, f[0][1] + f[2] * t - cy, (s["z0"] + s["z1"]) / 2
            tot += 1
            if not any(abs(g[0] - x) < 0.9 and abs(g[1] - y) < 0.9 and abs(g[2] - z) < 0.9 for g in cents):
                bad += 1
    print(f"{label}: {tot} ventanas, {bad} sin vidrio cerca")
    return bad


base = {"verts": [[0, 0], [20, 0], [20, 12], [0, 12]], "floors": 5, "floor_h": 3, "edges": [{}, {}, {}, {}]}
fails = check(dict(base), "sin retranqueo")
for lab, sb in (("frente", [3, 0, 0, 0]), ("dos lados", [3, 2, 0, 0]), ("todos", [2, 2, 2, 2]), ("asimétrico", [0, 4, 2, 0])):
    fails += check(dict(base, setbacks=[{"from": 2, "ins": sb}]), lab)
fails += check(dict(base, edges=[{}, {"bulge": 3}, {}, {}]), "pared curva")
print("OK" if fails == 0 else "FALLA")
sys.stdout.flush()
os._exit(1 if fails else 0)
