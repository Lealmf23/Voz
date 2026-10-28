#!/usr/bin/env python3
"""Corre todas las pruebas del addon, una por proceso, y muestra una tabla.

Uso:
  python3 tests/run_tests.py                 # todo
  python3 tests/run_tests.py uv_bake ao_bake # solo esos casos
  python3 tests/run_tests.py --update-baselines   # guarda las métricas actuales como línea base
Requiere el módulo bpy (pip install bpy) y numpy/Pillow.
"""
import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
TIMEOUT = 300
# métricas que forman la línea base: (caso, métrica en el resultado, nombre en baselines.json)
BASE_KEYS = [("uv_bake", "px_por_m", "uv_bake.px_por_m"), ("uv_bake", "islas", "uv_bake.islas"),
             ("perf_big", "px_por_m", "perf_big.px_por_m"), ("perf_big", "segundos", "perf_big.segundos")]


def run_case(name):
    t0 = time.time()
    try:
        p = subprocess.run([sys.executable, os.path.join(HERE, "cases.py"), name], cwd=HERE, capture_output=True, text=True, timeout=TIMEOUT)
    except subprocess.TimeoutExpired:
        return {"name": name, "ok": False, "msg": f"tiempo agotado ({TIMEOUT} s)", "secs": time.time() - t0, "data": {}}
    data = {}
    for line in p.stdout.splitlines():
        if line.startswith("RESULT "):
            data = json.loads(line[7:])
    msg = ""
    if p.returncode != 0:
        tail = [l for l in (p.stderr or "").strip().splitlines() if l.strip()]
        msg = next((l for l in reversed(tail) if "Error" in l), tail[-1] if tail else "falló sin mensaje")
    return {"name": name, "ok": p.returncode == 0, "msg": msg, "secs": time.time() - t0, "data": data}


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    upd = "--update-baselines" in sys.argv
    names = args or subprocess.run([sys.executable, os.path.join(HERE, "cases.py"), "--list"], cwd=HERE, capture_output=True, text=True).stdout.split()
    res = [run_case(n) for n in names]
    print(f"{'caso':<18}{'resultado':<11}{'seg':>6}  detalle")
    for r in res:
        det = r["msg"] if not r["ok"] else json.dumps(r["data"], ensure_ascii=False)[:90]
        print(f"{r['name']:<18}{'OK' if r['ok'] else 'FALLA':<11}{r['secs']:>6.1f}  {det}")
    if upd:
        base = {}
        for case, key, nm in BASE_KEYS:
            r = next((x for x in res if x["name"] == case and x["ok"]), None)
            if r and key in r["data"]:
                base[nm] = r["data"][key]
        with open(os.path.join(HERE, "baselines.json"), "w") as f:
            json.dump(base, f, indent=2, ensure_ascii=False)
        print("líneas base guardadas:", base)
    bad = [r for r in res if not r["ok"]]
    print(f"\n{len(res) - len(bad)}/{len(res)} pruebas OK" + ("" if not bad else " · FALLAN: " + ", ".join(r["name"] for r in bad)))
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
