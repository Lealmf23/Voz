"""
CS2 Generador de Edificios: add-on de Blender con panel lateral (residencial, comercial, oficinas, industrial, servicios y emblemáticos).

Instalación (dos formas):
  A) Edit > Preferences > Add-ons > Install from Disk... y elegir este archivo; activarlo.
  B) Abrirlo en el Text Editor de Blender y apretar "Run Script".
Después: Vista 3D > tecla N > pestaña "CS2".
"""
bl_info = {
    "name": "CS2 Generador de Edificios",
    "author": "Leo",
    "version": (2, 83, 0),
    "blender": (4, 2, 0),
    "location": "Vista 3D > Barra lateral (N) > CS2",
    "description": "Edificios low-poly de todo tipo (residencial, comercial, oficinas, industrial, servicios y emblemáticos) con LODs y FBX para Cities: Skylines 2",
    "category": "Add Mesh",
}

import json
import math
import re
import textwrap
import time
from bisect import bisect_left, bisect_right
import os
import random
import zlib
import sys
import platform
import traceback

import bpy
from bpy.app.handlers import persistent
import bmesh
from bpy.props import (BoolProperty, EnumProperty, FloatProperty, FloatVectorProperty,
                       IntProperty, PointerProperty, StringProperty)
from bpy_extras.io_utils import ExportHelper, ImportHelper
from mathutils import Vector, Matrix


# ----------------------------------------------------------------------------
# Registro de errores e informe (para diagnosticar sin depender de capturas)
# ----------------------------------------------------------------------------
_ERRLOG = []          # [{"t", "where", "msg", "tb", "n"}] (los repetidos suman en "n")
_TIMINGS = {}         # duración (s) de la última ejecución de cada paso largo
_PROG = {"name": "", "t0": 0.0, "last": 0.0, "busy": False, "frac": 0.0, "msg": "", "title": ""}

class _Work:
    """Contexto para operadores largos: muestra una barra de progreso (panel, barra superior y barra de estado) mientras dura.
    Uso: `with _Work(context, "Exportando") as w: w.step(0.3, "Texturas")`. Se puede anidar: el de afuera manda."""
    def __init__(self, context, title):
        self.ctx, self.title, self.outer = context, title, False

    def __enter__(self):
        self.outer = bool(_PROG["busy"])
        if not self.outer:
            _PROG.update(busy=True, frac=0.0, msg="Empezando", title=self.title, t0=time.time(), last=0.0)
            try:
                self.ctx.window_manager.progress_begin(0, 100)
            except Exception:
                pass
            self.step(0.0, "Empezando", force=True)
        return self

    def step(self, frac, msg=None, force=False):
        _prog_step(self.ctx, frac, msg, force)

    def sub(self, a, b):
        """Callback (frac, msg) que reparte el progreso de una función con `progress=` dentro del tramo [a, b]."""
        return lambda f, m="": _prog_step(self.ctx, a + (b - a) * max(0.0, min(1.0, f)), m)

    def __exit__(self, *exc):
        if not self.outer:
            _PROG.update(busy=False, frac=0.0, msg="")
            try:
                self.ctx.window_manager.progress_end()
                self.ctx.workspace.status_text_set(None)
            except Exception:
                pass
            _redraw_ui()
        return False


def _with_work(title):
    """Decorador de execute(): envuelve el operador en una barra de progreso visible."""
    def deco(fn):
        def wrapper(self, context):
            with _Work(context, title):
                return fn(self, context)
        wrapper.__name__, wrapper.__doc__ = fn.__name__, fn.__doc__
        return wrapper
    return deco


def _sub_cb(context, a, b):
    """Callback de progreso para una función con `progress=`, repartido en el tramo [a, b] de la barra activa."""
    return lambda f, m="": _prog_step(context, a + (b - a) * max(0.0, min(1.0, f)), m)


def _prog_step(context, frac, msg=None, force=False):
    """Actualiza la barra de progreso (si hay una activa) y repinta la interfaz como mucho cada 0,12 s."""
    if not _PROG["busy"]:
        return
    frac = max(0.0, min(1.0, float(frac)))
    _PROG["frac"] = max(_PROG["frac"], frac) if not force else frac
    if msg:
        _PROG["msg"] = msg
    try:
        context.window_manager.progress_update(int(_PROG["frac"] * 100))
        context.workspace.status_text_set(f"{_PROG['title']}: {_PROG['msg']} ({int(_PROG['frac'] * 100)}%)")
    except Exception:
        pass
    now = time.time()
    if force or now - _PROG["last"] > 0.12:
        _PROG["last"] = now
        _redraw_ui()


def _draw_work_bar(lay):
    """Barra de progreso (debajo de los botones) mientras corre una tarea larga."""
    if not _PROG["busy"]:
        return
    txt = f"{_PROG['title']}: {_PROG['msg']} · {int(_PROG['frac'] * 100)}%"
    if hasattr(lay, "progress"):
        lay.progress(factor=_PROG["frac"], type="BAR", text=txt)
    else:
        lay.label(text=txt, icon="SORTTIME")



def log_error(where, exc=None, note=""):
    """Anota un error con su traceback (y lo imprime una sola vez en la consola) en vez de tragárselo."""
    tb = traceback.format_exc() if exc is not None else ""
    msg = (f"{type(exc).__name__}: {exc}" if exc is not None else "") + (f" {note}" if note else "")
    for e in _ERRLOG:
        if e["where"] == where and e["msg"] == msg:
            e["n"] += 1
            e["t"] = time.time()
            return
    _ERRLOG.append({"t": time.time(), "where": where, "msg": msg, "tb": tb, "n": 1})
    del _ERRLOG[:-40]
    print(f"CS2 [{where}] {msg}")


def _log_exc(where):
    """Para los `except Exception:` que antes no hacían nada: anota el error y sigue."""
    log_error(where, sys.exc_info()[1] or Exception("?"))


def build_report():
    """Texto con lo necesario para entender un problema: versiones, ajustes, estado de las UV y errores recientes."""
    out = []
    ver = ".".join(str(x) for x in bl_info["version"])
    out.append(f"CS2 Generador de Edificios v{ver}")
    try:
        out.append(f"Blender {bpy.app.version_string} · {platform.system()} {platform.release()} · Python {platform.python_version()}")
    except Exception:
        pass
    try:
        s = bpy.context.scene.cs2_building
        out.append(f"Edificio: {s.building_name} · {s.floors} pisos · textura {s.tex_size} · horneado {'sí' if s.bake_ao else 'no'}")
        objs = _existing_lods(s)
        if not objs:
            out.append("LODs: no creados")
        elif len(objs) < 3:
            out.append("LOD1 y LOD2: sin generar (se generan en Exportar o al armar las UV)")
        for i, o in enumerate(objs):
            me = o.data
            d = uv_state(me)
            info = (d or {}).get("info") or {}
            out.append(f"LOD{i}: {mesh_tris(me)} triángulos, {len(me.polygons)} caras · UV: "
                       + (f"{info.get('islas', '?')} islas, {info.get('px_por_m', '?')} px/m, {info.get('uso', '?')}% en uso"
                          + (" (desactualizadas)" if d.get("stale") else "") if d else "sin generar"))
        out.append("Ajustes de UV: " + ", ".join(f"{k}={getattr(s, k)}" for k in
                   ("bake_share", "bake_align", "bake_align_x", "bake_share_loose", "bake_share_area", "bake_roof_wt", "bake_margin", "uv_smart", "uv_group", "uv_grid", "uv_same_density", "uv_c_wall", "uv_c_trim", "uv_c_roof", "uv_c_door", "uv_c_gar", "uv_c_rail")
                   if hasattr(s, k)))
    except Exception as e:
        out.append(f"(no pude leer el estado del edificio: {e})")
    if _TIMINGS:
        out.append("Tiempos: " + ", ".join(f"{k} {v:.1f}s" for k, v in _TIMINGS.items()))
    if _ERRLOG:
        out.append("")
        out.append(f"Errores recientes ({len(_ERRLOG)}):")
        for e in _ERRLOG[-8:]:
            out.append(f"- [{e['where']}] {e['msg']}" + (f" (x{e['n']})" if e["n"] > 1 else ""))
            if e["tb"]:
                out.append("  " + "\n  ".join(e["tb"].strip().splitlines()[-6:]))
    else:
        out.append("Sin errores registrados.")
    return "\n".join(out)


CELL = 8.0  # tamaño de celda de lote en metros (verificar contra el juego)
MAT_ORDER = ["facade", "glass", "trim", "roof", "door", "gls", "facb", "facc", "facd", "gar", "rail"]  # glass = ventanas (_Win), gls = vidrio suelto (_Gls)
MAT = {name: i for i, name in enumerate(MAT_ORDER)}


# ----------------------------------------------------------------------------
# Utilidades de geometría
# ----------------------------------------------------------------------------
def add_box(bm, cx, cy, cz, sx, sy, sz, mat, skip=()):
    """Caja alineada a los ejes. skip: lados a omitir, ej. ('+y',)."""
    hx, hy, hz = sx / 2, sy / 2, sz / 2
    v = [bm.verts.new((cx + dx * hx, cy + dy * hy, cz + dz * hz))
         for dz in (-1, 1) for dy in (-1, 1) for dx in (-1, 1)]
    sides = {
        "-z": (0, 2, 3, 1), "+z": (4, 5, 7, 6),
        "-y": (0, 1, 5, 4), "+y": (2, 6, 7, 3),
        "-x": (0, 4, 6, 2), "+x": (1, 3, 7, 5),
    }
    for name, idx in sides.items():
        if name in skip:
            continue
        f = bm.faces.new([v[i] for i in idx])
        f.material_index = mat


def add_ring(bm, w, d, outset, thick, z0, z1, mat):
    """Anillo rectangular (cornisa/parapet) hecho con 4 cajas."""
    ox, oy = w / 2 + outset, d / 2 + outset
    h, cz = z1 - z0, (z0 + z1) / 2
    for sy in (-1, 1):
        add_box(bm, 0, sy * (oy - thick / 2), cz, 2 * ox, thick, h, mat)
    for sx in (-1, 1):
        add_box(bm, sx * (ox - thick / 2), 0, cz, thick, 2 * oy - 2 * thick, h, mat)


def window_columns(length, win_w, min_gap):
    """Devuelve el u inicial de cada ventana, repartidas parejo sobre la fachada."""
    n = max(1, int((length - min_gap) // (win_w + min_gap)))
    gap = (length - n * win_w) / (n + 1)
    return [gap * (k + 1) + win_w * k for k in range(n)]


def rng_for(p, *key):
    """Generador al azar determinista: misma semilla + misma clave = mismo resultado."""
    return random.Random("|".join(str(x) for x in (p["seed"], *key)))


def has_balcony(p, f, k, wid=0):
    mode = p["balconies"]
    if f == 0 or mode == "none":
        return False
    if mode == "all":
        return True
    if mode == "alternate":
        return k % 2 == 0
    key = ("bal", f, k) if wid == 0 else ("bal", wid, f, k)
    return rng_for(p, *key).random() < p["balcony_prob"]  # "random"


def load_pieces(coll):
    """Lee las piezas (ventanas/puertas) de una colección.

    Convención: pieza modelada en 2 m x 2 m, origen en el centro de la base
    (x entre -1 y 1, z entre 0 y 2), el exterior hacia -Y y la profundidad hacia +Y.
    Se usa la geometría local del objeto (sin su transformación ni modificadores).
    """
    pieces = []
    if coll is None:
        return pieces
    for obj in sorted(coll.all_objects, key=lambda o: o.name):
        if obj.type != "MESH":
            continue
        me = obj.data
        uvl = me.uv_layers.active
        polys = []
        for poly in me.polygons:
            mat = me.materials[poly.material_index] if poly.material_index < len(me.materials) else None
            uvs = [tuple(uvl.data[li].uv) for li in poly.loop_indices] if uvl else None
            polys.append((list(poly.vertices), mat, uvs))
        pieces.append({"name": obj.name, "verts": [v.co.copy() for v in me.vertices], "polys": polys})
    return pieces


def pieces_for(p, mat):
    if mat == MAT["gls"]:
        return []
    return p.get("door_pieces" if mat == MAT["door"] else "window_pieces") or []


def mat_index_for(p, mat):
    """Índice de material para una pieza: los materiales extra van después de los 5 base."""
    if mat is None:
        return MAT["trim"]
    extra = p["_extra_mats"]
    if mat not in extra:
        extra.append(mat)
    return len(MAT_ORDER) + extra.index(mat)


def make_openings(p, length, front, lod, fid):
    """Aberturas de una fachada: lista de (u0, u1, z0, z1, material)."""
    if lod >= 2:
        return []
    ww, wh, fh = p["win_w"], p["win_h"], p["floor_h"]
    var = p["variation"]
    cols = window_columns(length, ww, p["min_gap"])
    door_col = len(cols) // 2
    ops = []
    for f in range(p["floors"]):
        for k, u0 in enumerate(cols):
            if front and f == 0 and k == door_col:
                dw = min(max(ww, 1.2), 1.5)   # la puerta no se ensancha con ventanas muy grandes
                du = u0 + (ww - dw) / 2
                ops.append((du, du + dw, 0.0, p["door_h"], MAT["door"]))
            elif front and has_balcony(p, f, k):
                z0 = f * fh
                ops.append((u0, u0 + ww, z0, z0 + 2.1, MAT["glass"]))
            else:
                h = wh
                if var > 0:
                    h = wh * (1 + (rng_for(p, "win", fid, f, k).random() - 0.5) * 0.6 * var)
                    h = max(0.6, min(h, fh - p["sill"] - 0.3))
                z0 = f * fh + p["sill"]
                ops.append((u0, u0 + ww, z0, z0 + h, MAT["glass"]))
    return ops


def insert_piece(bm, piece, o, u, n, op, p):
    """Coloca una pieza escalada al hueco: x -> a lo largo de la fachada, y -> hacia adentro, z -> arriba."""
    u0, u1, z0, z1, _m = op
    sx, sz = (u1 - u0) / 2.0, (z1 - z0) / 2.0
    base = o + u * ((u0 + u1) / 2.0) + Vector((0, 0, z0))
    zaxis = Vector((0, 0, 1))
    uvl = bm.loops.layers.uv.active
    flag = bm.faces.layers.int.get("is_piece")
    verts = [bm.verts.new(base + u * (c.x * sx) - n * c.y + zaxis * (c.z * sz)) for c in piece["verts"]]
    for idxs, mat, uvs in piece["polys"]:
        try:
            f = bm.faces.new([verts[i] for i in idxs])
        except ValueError:
            continue
        f.material_index = mat_index_for(p, mat)
        if uvs and uvl:
            for loop, uv in zip(f.loops, uvs):
                loop[uvl].uv = uv
            if flag:
                f[flag] = 1  # conserva las UVs de la pieza


# ----------------------------------------------------------------------------
# Estilos de ventanas y puertas: paramétricos, editables y guardables en una biblioteca
# ----------------------------------------------------------------------------
STYLE_DEFAULT = {
    "kind": "window",        # window | door
    "shape": "RECT",         # RECT ARCH POINTED CIRCLE OVAL OCTAGON DIAMOND
    "arch_rise": 0.5,        # alto del arco como fracción del ancho (0.5 = medio punto)
    "frame_w": 0.06, "frame_d": 0.06,
    "cols": 1, "rows": 1, "bar_w": 0.035, "bar_d": 0.03,
    "transom": 0.0,          # travesaño: altura relativa (0 = sin travesaño)
    "lattice": 0,            # celosía de rombos (0 = no)
    "casing_w": 0.0, "casing_d": 0.05,
    "head": "NONE",          # NONE LINTEL CORNICE PEDIMENT KEYSTONE
    "head_h": 0.25,
    "sill": "NONE",          # NONE SIMPLE CORBEL BALCONETTE
    "sill_d": 0.12,
    "shutters": "NONE",      # NONE OPEN CLOSED
    "louvers": 6,
    "pilasters": False, "pilaster_w": 0.12,
    # puertas
    "leaves": 1,
    "leaf_type": "PANELS",   # FLUSH PANELS PLANK BRACED SLATS LOUVER
    "panels": 2,
    "lite": "NONE",          # NONE TOP FULL OVAL ROUND SLOTS GRID
    "lite_n": 3,
    "glass_frac": 0.4,       # para lite=TOP
    "sidelights": 0.0,       # ancho de los laterales vidriados (m)
    "handle": True,
    "garage": False,         # portón de garaje: la hoja usa el material de portones
    "size_w": 0.0, "size_h": 0.0,   # medidas propias de la puerta (0 = las generales)
    # balcones
    "bal_type": "SLAB",      # SLAB (volado) | BAY (mirador vidriado)
    "bal_front": "RECT",     # RECT CHAMFER ROUNDED CURVED
    "bal_depth": 1.2, "bal_extra_w": 0.8, "bal_slab_t": 0.18,
    "bal_support": "NONE",   # NONE CORBELS BRACKETS BEAM
    "bal_rail": "BALUSTERS",  # NONE SOLID BALUSTERS RODS GLASS LATTICE WROUGHT
    "bal_rail_h": 0.95, "bal_bar_gap": 0.14,
    "bal_sides": True, "bal_cap": True,
    "bal_roof": "NONE",      # NONE AWNING FLAT
    "bal_planter": False, "bal_planter_h": 0.3,
    "bal_run": False,        # balcón corrido: una sola losa y baranda que une los balcones del piso (y sigue las curvas)
    "bal_bay_h": 2.2,
    "bal_bar_r": 0.014, "bal_post_r": 0.028,   # grosor de los barrotes y de los postes de esquina/extremo
    "bal_cap_w": 0.09, "bal_cap_h": 0.05,      # sección del pasamanos
    # muros cortina (kind = curtain): mismas claves que los ajustes del muro
    "cw_mw": 1.5, "cw_mh": 0.0, "cw_pt": 0.06, "cw_pd": 0.08, "cw_ht": 0.0, "cw_hd": 0.0, "cw_prof": 0,
    "cw_mmat": 4, "cw_pat": 0, "cw_patn": 3, "cw_patk": 0.6,
    "cw_band": 0.45, "cw_bmat": 4, "cw_edge": 0.0, "cw_finn": 0, "cw_find": 0.35, "cw_fint": 0.12, "cw_fmat": 1,
    "cw_doorw": 0.0, "cw_baseh": 0.0, "cw_basemat": 4, "cw_caph": 0.0, "cw_capo": 0.0, "cw_capmat": 4, "cw_cornw": 0.0,
}
CW_STYLE_KEYS = [k for k in STYLE_DEFAULT if k.startswith("cw_")]

STYLE_SHAPES = [("RECT", "Rectangular", ""), ("ARCH", "Arco", "Medio punto o rebajado según la altura del arco"),
                ("POINTED", "Ojival (gótico)", ""), ("CIRCLE", "Circular", ""), ("OVAL", "Ovalada", ""),
                ("OCTAGON", "Octogonal", ""), ("DIAMOND", "Rombo", "")]
STYLE_HEADS = [("NONE", "Sin remate", ""), ("LINTEL", "Dintel", ""), ("CORNICE", "Cornisa", ""),
               ("PEDIMENT", "Frontón triangular", ""), ("KEYSTONE", "Clave", "")]
STYLE_SILLS = [("NONE", "Sin repisa", ""), ("SIMPLE", "Repisa simple", ""), ("CORBEL", "Con ménsulas", ""),
               ("BALCONETTE", "Balconcito con baranda", "")]
STYLE_SHUTTERS = [("NONE", "Sin persianas", ""), ("OPEN", "Abiertas a los lados", ""), ("CLOSED", "Cerradas", "")]
STYLE_LEAVES = [("FLUSH", "Lisa", ""), ("PANELS", "Con cuarterones", ""), ("PLANK", "De tablones", ""),
                ("BRACED", "Tablones con cruz", ""), ("SLATS", "Tiras horizontales (portón)", ""),
                ("LOUVER", "De persiana", ""), ("SECTIONAL", "Seccional (garaje)", ""),
                ("ROLLER", "Enrollable (garaje)", "")]
STYLE_BAL_TYPES = [("SLAB", "Balcón volado", ""), ("BAY", "Mirador vidriado", ""),
                   ("LOGGIA", "Loggia (embutido, no sobresale)", "")]
STYLE_BAL_FRONTS = [("RECT", "Recto", ""), ("CHAMFER", "Facetado", ""), ("ROUNDED", "Redondeado", ""),
                    ("CURVED", "Curvo suave", "")]
STYLE_BAL_SUPPORTS = [("NONE", "Sin apoyo visible", ""), ("CORBELS", "Ménsulas", ""), ("BRACKETS", "Escuadras", ""),
                      ("BEAM", "Viga de borde", "")]
STYLE_BAL_RAILS = [("NONE", "Sin baranda", ""), ("SOLID", "Antepecho macizo", ""), ("BALUSTERS", "Balaustres", ""),
                   ("RODS", "Barras horizontales", ""), ("GLASS", "Vidrio", ""), ("LATTICE", "Celosía de rombos", ""),
                   ("WROUGHT", "Herrería", "")]
STYLE_BAL_ROOFS = [("NONE", "Sin techo", ""), ("AWNING", "Toldo inclinado", ""), ("FLAT", "Techo plano con pilares", "")]
STYLE_LITES = [("NONE", "Sin vidrio", ""), ("TOP", "Vidrio arriba", ""), ("FULL", "Vidrio completo", ""),
               ("OVAL", "Óvalo", ""), ("ROUND", "Ojo de buey", ""), ("SLOTS", "Ranuras verticales", ""),
               ("GRID", "Vidrios cuadrados", "")]


# ---- geometría 2D (x a lo largo de la pared, z hacia arriba) ----------------
def style_outline(shape, w, h, rise, seg=10):
    """Contorno convexo antihorario dentro de [-w/2, w/2] x [0, h]."""
    hw = w / 2.0
    if shape in ("ARCH", "POINTED"):
        r = max(0.08, min(rise * w, h - 0.25))
        zs = h - r
        pts = [(-hw, 0.0), (hw, 0.0), (hw, zs)]
        if shape == "ARCH":
            R = (hw * hw + r * r) / (2.0 * r)
            cz = zs + r - R
            a0 = math.atan2(zs - cz, hw)
            a1 = math.pi - a0
            for i in range(1, seg):
                a = a0 + (a1 - a0) * i / seg
                pts.append((R * math.cos(a), cz + R * math.sin(a)))
        else:
            c = (hw * hw - r * r) / (2.0 * hw)
            R = hw - c
            ta = math.atan2(r, -c)
            half = max(3, seg // 2)
            right = [(c + R * math.cos(ta * i / half), zs + R * math.sin(ta * i / half))
                     for i in range(1, half + 1)]
            right[-1] = (0.0, zs + r)
            pts += right
            pts += [(-x, z) for x, z in reversed(right[:-1])]
        pts.append((-hw, zs))
        return pts
    if shape in ("CIRCLE", "OVAL"):
        rx, rz = hw, h / 2.0
        if shape == "CIRCLE":
            rx = rz = min(hw, h / 2.0)
        n = 16
        return [(rx * math.cos(2 * math.pi * i / n), h / 2.0 + rz * math.sin(2 * math.pi * i / n))
                for i in range(n)]
    if shape == "OCTAGON":
        c = 0.3 * min(w, h)
        return [(-hw + c, 0.0), (hw - c, 0.0), (hw, c), (hw, h - c), (hw - c, h), (-hw + c, h),
                (-hw, h - c), (-hw, c)]
    if shape == "DIAMOND":
        return [(0.0, 0.0), (hw, h / 2.0), (0.0, h), (-hw, h / 2.0)]
    return [(-hw, 0.0), (hw, 0.0), (hw, h), (-hw, h)]


def offset_poly(pts, d):
    """Desplaza un polígono convexo antihorario: d > 0 hacia afuera, d < 0 hacia adentro."""
    n = len(pts)
    out = []
    for i in range(n):
        p0, p1, p2 = pts[i - 1], pts[i], pts[(i + 1) % n]
        e1 = (p1[0] - p0[0], p1[1] - p0[1])
        e2 = (p2[0] - p1[0], p2[1] - p1[1])
        l1 = math.hypot(*e1) or 1.0
        l2 = math.hypot(*e2) or 1.0
        n1 = (e1[1] / l1, -e1[0] / l1)
        n2 = (e2[1] / l2, -e2[0] / l2)
        k = max(1.0 + n1[0] * n2[0] + n1[1] * n2[1], 0.6)
        out.append((p1[0] + d * (n1[0] + n2[0]) / k, p1[1] + d * (n1[1] + n2[1]) / k))
    return out


def clip_poly(poly, axis, val, keep_greater):
    """Recorta un polígono convexo contra x = val (axis 0) o z = val (axis 1)."""
    out = []
    n = len(poly)
    for i in range(n):
        a, b = poly[i], poly[(i + 1) % n]
        ina = (a[axis] >= val) if keep_greater else (a[axis] <= val)
        inb = (b[axis] >= val) if keep_greater else (b[axis] <= val)
        if ina:
            out.append(a)
        if ina != inb and abs(b[axis] - a[axis]) > 1e-9:
            t = (val - a[axis]) / (b[axis] - a[axis])
            out.append((a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1])))
    return out


def clip_line(poly, p, d):
    """Parámetros (t0, t1) del tramo de la recta p + t*d dentro de un polígono convexo antihorario."""
    t0, t1 = -1e9, 1e9
    n = len(poly)
    for i in range(n):
        a, b = poly[i], poly[(i + 1) % n]
        ex, ez = b[0] - a[0], b[1] - a[1]
        num = ex * (p[1] - a[1]) - ez * (p[0] - a[0])
        den = ex * d[1] - ez * d[0]
        if abs(den) < 1e-9:
            if num < 0:
                return None
        else:
            t = -num / den
            if den > 0:
                t0 = max(t0, t)
            else:
                t1 = min(t1, t)
    return (t0, t1) if t0 < t1 else None


def poly_bounds(poly):
    xs = [q[0] for q in poly]
    zs = [q[1] for q in poly]
    return min(xs), max(xs), min(zs), max(zs)


def poly_area(poly):
    return abs(sum(poly[i][0] * poly[(i + 1) % len(poly)][1] - poly[(i + 1) % len(poly)][0] * poly[i][1]
                   for i in range(len(poly)))) / 2.0


def ellipse_poly(cx, cz, rx, rz, n=14):
    return [(cx + rx * math.cos(2 * math.pi * i / n), cz + rz * math.sin(2 * math.pi * i / n)) for i in range(n)]


def rect_poly(x0, x1, z0, z1):
    return [(x0, z0), (x1, z0), (x1, z1), (x0, z1)]


# ---- constructor ---------------------------------------------------------------
def style_tools(bm, base, u, n):
    """Herramientas de dibujo en coordenadas locales: x a lo largo de la pared, y hacia adentro, z arriba."""
    zax = Vector((0, 0, 1))
    def P(x, y, z):
        return bm.verts.new(base + u * x - n * y + zax * z)

    def face(pts, mat, want):
        if len(pts) < 3:
            return
        nx = ny = nz = 0.0
        for i in range(len(pts)):
            a, b = pts[i], pts[(i + 1) % len(pts)]
            nx += (a[1] - b[1]) * (a[2] + b[2])
            ny += (a[2] - b[2]) * (a[0] + b[0])
            nz += (a[0] - b[0]) * (a[1] + b[1])
        if nx * nx + ny * ny + nz * nz < 1e-10:
            return
        if nx * want[0] + ny * want[1] + nz * want[2] < 0:
            pts = pts[::-1]
        try:
            f = bm.faces.new([P(*q) for q in pts])
        except ValueError:
            return
        f.material_index = mat

    FRONT = (0.0, -1.0, 0.0)

    def poly_face(poly, y, mat):
        face([(x, y, z) for x, z in poly], mat, FRONT)

    def box(xc, yc, zc, sx, sy, sz, mat):
        x0_, x1_, y0_, y1_, z0_, z1_ = xc - sx / 2, xc + sx / 2, yc - sy / 2, yc + sy / 2, zc - sz / 2, zc + sz / 2
        face([(x0_, y0_, z0_), (x1_, y0_, z0_), (x1_, y0_, z1_), (x0_, y0_, z1_)], mat, FRONT)
        face([(x0_, y0_, z1_), (x1_, y0_, z1_), (x1_, y1_, z1_), (x0_, y1_, z1_)], mat, (0, 0, 1))
        face([(x0_, y0_, z0_), (x1_, y0_, z0_), (x1_, y1_, z0_), (x0_, y1_, z0_)], mat, (0, 0, -1))
        face([(x0_, y0_, z0_), (x0_, y1_, z0_), (x0_, y1_, z1_), (x0_, y0_, z1_)], mat, (-1, 0, 0))
        face([(x1_, y0_, z0_), (x1_, y1_, z0_), (x1_, y1_, z1_), (x1_, y0_, z1_)], mat, (1, 0, 0))

    def bar(p0, p1, bw, bd, yb, mat):
        dx, dz = p1[0] - p0[0], p1[1] - p0[1]
        ln = math.hypot(dx, dz)
        if ln < 0.04:
            return
        dx, dz = dx / ln, dz / ln
        qx, qz = -dz * bw / 2, dx * bw / 2
        a, b = (p0[0] + qx, p0[1] + qz), (p1[0] + qx, p1[1] + qz)
        c, d = (p1[0] - qx, p1[1] - qz), (p0[0] - qx, p0[1] - qz)
        yf = yb - bd
        face([(a[0], yf, a[1]), (b[0], yf, b[1]), (c[0], yf, c[1]), (d[0], yf, d[1])], mat, FRONT)
        face([(a[0], yf, a[1]), (b[0], yf, b[1]), (b[0], yb, b[1]), (a[0], yb, a[1])], mat, (qx, 0, qz))
        face([(d[0], yf, d[1]), (c[0], yf, c[1]), (c[0], yb, c[1]), (d[0], yb, d[1])], mat, (-qx, 0, -qz))
        face([(b[0], yf, b[1]), (c[0], yf, c[1]), (c[0], yb, c[1]), (b[0], yb, b[1])], mat, (dx, 0, dz))
        face([(a[0], yf, a[1]), (d[0], yf, d[1]), (d[0], yb, d[1]), (a[0], yb, a[1])], mat, (-dx, 0, -dz))

    def ring(outer, inner, yf, yb, mat, outer_sides=False, inner_sides=False):
        m = len(outer)
        for i in range(m):
            j = (i + 1) % m
            face([(outer[i][0], yf, outer[i][1]), (outer[j][0], yf, outer[j][1]),
                  (inner[j][0], yf, inner[j][1]), (inner[i][0], yf, inner[i][1])], mat, FRONT)
            if outer_sides:
                ex, ez = outer[j][0] - outer[i][0], outer[j][1] - outer[i][1]
                face([(outer[i][0], yf, outer[i][1]), (outer[j][0], yf, outer[j][1]),
                      (outer[j][0], yb, outer[j][1]), (outer[i][0], yb, outer[i][1])], mat, (ez, 0, -ex))
            if inner_sides:
                ex, ez = inner[j][0] - inner[i][0], inner[j][1] - inner[i][1]
                face([(inner[i][0], yf, inner[i][1]), (inner[j][0], yf, inner[j][1]),
                      (inner[j][0], yb, inner[j][1]), (inner[i][0], yb, inner[i][1])], mat, (-ez, 0, ex))
    return P, face, poly_face, box, bar, ring, FRONT


def insert_style(bm, st, o, u, n, op, depth, p):
    """Genera en `bm` la ventana o puerta de un estilo, a la medida de la abertura `op`."""
    s = dict(STYLE_DEFAULT)
    s.update(st)
    u0, u1, z0, z1, _m = op
    w, h = u1 - u0, z1 - z0
    if w < 0.3 or h < 0.3:
        return
    base = o + u * ((u0 + u1) / 2.0) + Vector((0, 0, z0))
    is_door = s["kind"] == "door"

    P, face, poly_face, box, bar, ring, FRONT = style_tools(bm, base, u, n)

    gy = depth - 0.01
    fw = min(s["frame_w"], 0.4 * min(w, h))
    fd = s["frame_d"]
    cw, cd = s["casing_w"], s["casing_d"]
    outer = style_outline(s["shape"], w, h, s["arch_rise"])
    inner = offset_poly(outer, -fw) if fw > 0.005 else outer
    bx0, bx1, bz0, bz1 = poly_bounds(inner)
    TRIM, GLASS, DOOR = MAT["trim"], MAT["glass"], MAT["door"]
    if not is_door:
        TRIM = MAT[SURF_MATS.get(int((p.get("look") or {}).get("frm_mat", 1)), "trim")]

    if fw > 0.005:
        ring(outer, inner, gy - fd, gy, TRIM, inner_sides=True)

    def small_frame(poly, depthf=0.03, wf=0.03):
        """Marquito alrededor de un vidrio chico."""
        if poly_area(poly) < 0.01:
            return
        inn = offset_poly(poly, -wf)
        ring(poly, inn, gy - depthf - 0.012, gy - 0.012, TRIM)

    def glazing_bars(region, yb):
        """Divisiones: columnas, filas, travesaño y celosía recortadas al contorno."""
        rx0, rx1, rz0, rz1 = poly_bounds(region)
        bw, bd = s["bar_w"], s["bar_d"]
        cols, rows = max(1, int(s["cols"])), max(1, int(s["rows"]))
        for k in range(1, cols):
            x = rx0 + (rx1 - rx0) * k / cols
            ts = clip_line(region, (x, rz0 - 1.0), (0.0, 1.0))
            if ts:
                bar((x, rz0 - 1.0 + ts[0]), (x, rz0 - 1.0 + ts[1]), bw, bd, yb, TRIM)
        for k in range(1, rows):
            z = rz0 + (rz1 - rz0) * k / rows
            ts = clip_line(region, (rx0 - 1.0, z), (1.0, 0.0))
            if ts:
                bar((rx0 - 1.0 + ts[0], z), (rx0 - 1.0 + ts[1], z), bw, bd, yb, TRIM)
        if s["transom"] > 0 and not is_door:
            z = rz0 + (rz1 - rz0) * s["transom"]
            ts = clip_line(region, (rx0 - 1.0, z), (1.0, 0.0))
            if ts:
                bar((rx0 - 1.0 + ts[0], z), (rx0 - 1.0 + ts[1], z), bw * 1.6, bd * 1.3, yb, TRIM)
        nl = min(6, int(s["lattice"]))
        if nl > 0:
            sx, sz = (rx1 - rx0) / nl, (rz1 - rz0) / nl
            for k in range(-nl, nl + 1):
                for dirx in (1.0, -1.0):
                    start = (rx0 + k * sx, rz0) if dirx > 0 else (rx0 + (k + nl) * sx, rz0)
                    if dirx < 0 and not (0 <= k + nl <= 2 * nl):
                        continue
                    ts = clip_line(region, start, (dirx * sx, sz))
                    if ts and ts[1] - ts[0] > 0.02:
                        bar((start[0] + dirx * sx * ts[0], start[1] + sz * ts[0]),
                            (start[0] + dirx * sx * ts[1], start[1] + sz * ts[1]), bw * 0.8, bd * 0.8, yb, TRIM)

    # ---------------- cuerpo: ventana o puerta ----------------
    if not is_door:
        poly_face(inner, gy, GLASS)
        glazing_bars(inner, gy)
    else:
        build_door_body(s, inner, bx0, bx1, bz0, bz1, h, w, gy, poly_face, box, bar, small_frame,
                        glazing_bars, offset_poly, clip_poly, ellipse_poly, rect_poly, poly_area)

    # ---------------- moldura exterior ----------------
    if cw > 0.005:
        outer2 = offset_poly(outer, cw)
        if is_door:
            outer2 = [(x, max(z, 0.0)) for x, z in outer2]
        ring(outer2, outer, -cd, 0.0, TRIM, outer_sides=True)
    top = h + (cw if cw > 0.005 else 0.0)
    over = w + 2 * (cw if cw > 0.005 else 0.0) + 0.16
    hd = cd + 0.04
    hh = s["head_h"]
    head = s["head"]
    if head == "LINTEL":
        box(0, -hd / 2, top + hh / 2, over, hd, hh, TRIM)
    elif head == "CORNICE":
        box(0, -hd / 2, top + hh * 0.3, over, hd, hh * 0.6, TRIM)
        box(0, -(hd + 0.05) / 2, top + hh * 0.6 + 0.03, over + 0.1, hd + 0.05, 0.06, TRIM)
    elif head == "PEDIMENT":
        ph = hh * 2.0
        hw_ = over / 2
        face([(-hw_, -hd, top), (hw_, -hd, top), (0, -hd, top + ph)], TRIM, FRONT)
        face([(-hw_, -hd, top), (0, -hd, top + ph), (0, 0, top + ph), (-hw_, 0, top)], TRIM, (-ph, 0, hw_))
        face([(hw_, -hd, top), (hw_, 0, top), (0, 0, top + ph), (0, -hd, top + ph)], TRIM, (ph, 0, hw_))
        box(0, -hd / 2, top + 0.03, over, hd, 0.06, TRIM)
    elif head == "KEYSTONE":
        box(0, -(cd + 0.08) / 2, h - hh * 0.4, max(0.14, w * 0.12), cd + 0.08, hh * 1.4, TRIM)

    sd = s["sill_d"]
    sill = s["sill"]
    if sill != "NONE" and not is_door:
        sw_ = w + 2 * (cw if cw > 0.005 else 0.0) + 0.14
        box(0, -sd / 2 + 0.01, -0.03, sw_, sd + 0.02, 0.06, TRIM)
        if sill == "CORBEL":
            for sx in (-1, 1):
                box(sx * (w / 2 + (cw if cw > 0.005 else 0.0) - 0.02), -sd * 0.4, -0.13, 0.08, sd * 0.8, 0.14, TRIM)
        elif sill == "BALCONETTE":
            zt_, zb_ = min(0.85, h - 0.2), 0.12
            rw = w + 0.12
            box(0, -sd, zt_, rw, 0.045, 0.045, TRIM)
            box(0, -sd, zb_, rw, 0.04, 0.04, TRIM)
            nb = min(14, max(3, int(w / 0.12)))
            for i in range(nb + 1):
                x = -rw / 2 + 0.02 + (rw - 0.04) * i / nb
                box(x, -sd, (zt_ + zb_) / 2, 0.022, 0.022, zt_ - zb_, TRIM)
            for sx in (-1, 1):
                box(sx * (rw / 2 - 0.02), -sd / 2, (zt_ + zb_) / 2, 0.04, sd, zt_ - zb_ + 0.04, TRIM)

    shut = s["shutters"]
    if shut != "NONE" and not is_door:
        sw2 = w / 2 * 0.94
        nlv = max(0, min(10, int(s["louvers"])))
        for sx in (-1, 1):
            if shut == "OPEN":
                xc, yc = sx * (w / 2 + cw + 0.04 + sw2 / 2), -0.02
            else:
                xc, yc = sx * (w / 4), -(cd + 0.05)
                sw2 = w / 2 - 0.02
            box(xc, yc, h / 2, sw2, 0.04, h, DOOR)
            for i in range(nlv):
                box(xc, yc - 0.03, (i + 0.5) * h / max(nlv, 1), sw2 * 0.88, 0.02, 0.03, DOOR)

    if s["pilasters"]:
        pw = s["pilaster_w"]
        for sx in (-1, 1):
            xc = sx * (w / 2 + cw + pw / 2 + 0.01)
            box(xc, -0.04, h / 2, pw, 0.08, h, TRIM)
            box(xc, -0.05, h + 0.03, pw + 0.05, 0.1, 0.06, TRIM)
            box(xc, -0.05, 0.04, pw + 0.04, 0.1, 0.08, TRIM)


def build_door_body(s, inner, bx0, bx1, bz0, bz1, h, w, gy, poly_face, box, bar, small_frame,
                    glazing_bars, offset_poly, clip_poly, ellipse_poly, rect_poly, poly_area):
    """Hoja(s) de puerta: tipo de hoja, vidrios, montante, laterales y manija."""
    TRIM, GLASS, DOOR = MAT["trim"], MAT["gls"], MAT["door"]
    leaves = 2 if int(s["leaves"]) >= 2 else 1
    region = inner
    side = max(0.0, s["sidelights"])
    # laterales vidriados
    if side > 0.1 and (bx1 - bx0) > side * 2 + 0.5:
        for sgn in (-1, 1):
            edge = sgn * (bx1 - side) if sgn > 0 else bx0 + side
            part = clip_poly(region, 0, edge, sgn < 0)
            if len(part) >= 3 and poly_area(part) > 0.01:
                poly_face(part, gy - 0.004, GLASS)
            xline = (bx0 + side) if sgn < 0 else (bx1 - side)
            bar((xline, bz0), (xline, bz1), 0.05, 0.04, gy, TRIM)
        region = clip_poly(clip_poly(region, 0, bx0 + side, True), 0, bx1 - side, False)
    rx0, rx1, rz0, rz1 = poly_bounds(region)
    # montante (vidrio encima de la hoja)
    zt = None
    if s["transom"] > 0:
        zt = bz0 + (bz1 - bz0) * s["transom"]
        upper = clip_poly(region, 1, zt, True)
        if len(upper) >= 3 and poly_area(upper) > 0.01:
            poly_face(upper, gy - 0.004, GLASS)
            glazing_bars(upper, gy)
        a0, a1 = poly_x_at(region, zt)
        bar((a0, zt), (a1, zt), 0.06, 0.04, gy, TRIM)
        region = clip_poly(region, 1, zt, False)
        rx0, rx1, rz0, rz1 = poly_bounds(region)
    if len(region) < 3:
        return
    GAR = bool(s.get("garage"))
    if GAR:
        DOOR = MAT["gar"]
    poly_face(region, gy, DOOR)
    lw = (rx1 - rx0) / leaves
    if leaves == 2:
        bar(((rx0 + rx1) / 2, rz0), ((rx0 + rx1) / 2, rz1), 0.03, 0.035, gy, TRIM)
    # vidrios de la hoja
    lite = s["lite"]
    lite_zmin = rz1
    lh = rz1 - rz0
    for li in range(leaves):
        lx0 = rx0 + li * lw
        lx1 = lx0 + lw
        cx = (lx0 + lx1) / 2
        lite_polys = []
        if lite in ("TOP", "FULL"):
            zc = rz0 + lh * (1.0 - (s["glass_frac"] if lite == "TOP" else 1.0))
            part = clip_poly(clip_poly(clip_poly(region, 0, lx0 + 0.06, True), 0, lx1 - 0.06, False), 1,
                             zc + (0.06 if lite == "TOP" else 0.12), True)
            if len(part) >= 3:
                lite_polys.append(offset_poly(part, -0.04) if lite == "FULL" else part)
                lite_zmin = min(lite_zmin, zc)
        elif lite == "OVAL":
            lite_polys.append(ellipse_poly(cx, rz0 + lh * 0.66, lw * 0.28, min(lh * 0.14, lw * 0.4) * 1.5))
            lite_zmin = min(lite_zmin, rz0 + lh * 0.5)
        elif lite == "ROUND":
            r = min(lw * 0.28, lh * 0.12)
            lite_polys.append(ellipse_poly(cx, rz0 + lh * 0.68, r, r))
            lite_zmin = min(lite_zmin, rz0 + lh * 0.5)
        elif lite == "SLOTS":
            nn = max(1, min(6, int(s["lite_n"])))
            sw_ = min(0.09, lw / (nn * 2 + 1))
            for k in range(nn):
                x = lx0 + lw * (k + 1) / (nn + 1)
                lite_polys.append(rect_poly(x - sw_ / 2, x + sw_ / 2, rz0 + lh * 0.45, rz0 + lh * 0.9))
            lite_zmin = min(lite_zmin, rz0 + lh * 0.4)
        elif lite == "GRID":
            nn = max(1, min(6, int(s["lite_n"])))
            sq = min(lw * 0.5, lh * 0.3 / nn)
            for k in range(nn):
                zc_ = rz0 + lh * 0.62 + (k + 0.5) * (lh * 0.3 / nn)
                lite_polys.append(rect_poly(cx - sq / 2, cx + sq / 2, zc_ - sq / 2, zc_ + sq / 2))
            lite_zmin = min(lite_zmin, rz0 + lh * 0.58)
        for lp in lite_polys:
            if len(lp) >= 3 and poly_area(lp) > 0.005:
                poly_face(lp, gy - 0.012, GLASS)
                small_frame(lp)
                if lite == "TOP" or lite == "FULL":
                    glazing_bars(lp, gy - 0.012)
    # tipo de hoja
    ltype = s["leaf_type"]
    ztop_panels = min(lite_zmin - 0.08, rz1 - 0.1) if lite != "NONE" else rz1 - 0.12
    if lite == "FULL":
        ztop_panels = rz0
    px_low = rz0 + 0.1
    for li in range(leaves):
        lx0 = rx0 + li * lw
        lx1 = lx0 + lw
        cx = (lx0 + lx1) / 2
        if ltype == "PANELS" and ztop_panels - px_low > 0.25:
            npn = max(1, min(4, int(s["panels"])))
            ph_ = (ztop_panels - px_low - 0.08 * (npn - 1)) / npn
            for k in range(npn):
                zc_ = px_low + ph_ / 2 + k * (ph_ + 0.08)
                if ph_ > 0.08 and lw - 0.3 > 0.1:
                    box(cx, gy - 0.015, zc_, lw - 0.3, 0.03, ph_, TRIM)
        elif ltype == "PLANK":
            npl = max(2, min(12, int((lx1 - lx0) / 0.16)))
            for k in range(1, npl):
                x = lx0 + (lx1 - lx0) * k / npl
                ts = clip_line(region, (x, rz0 - 1.0), (0.0, 1.0))
                if ts:
                    bar((x, rz0 - 1.0 + ts[0]), (x, rz0 - 1.0 + ts[1]), 0.012, 0.012, gy, TRIM)
        elif ltype == "BRACED":
            npl = max(2, min(8, int((lx1 - lx0) / 0.2)))
            for k in range(1, npl):
                x = lx0 + (lx1 - lx0) * k / npl
                ts = clip_line(region, (x, rz0 - 1.0), (0.0, 1.0))
                if ts:
                    bar((x, rz0 - 1.0 + ts[0]), (x, rz0 - 1.0 + ts[1]), 0.012, 0.012, gy, TRIM)
            m = 0.12
            zlo, zhi = rz0 + m, min(rz1, rz0 + lh * 0.82) - m
            bar((lx0 + m, zlo), (lx1 - m, zhi), 0.09, 0.035, gy - 0.01, TRIM)
            bar((lx0 + m, zhi), (lx1 - m, zlo), 0.09, 0.035, gy - 0.01, TRIM)
            for z in (zlo, zhi):
                bar((lx0 + m * 0.5, z), (lx1 - m * 0.5, z), 0.09, 0.035, gy - 0.01, TRIM)
        elif ltype == "SECTIONAL":
            top_ = (ztop_panels if lite != "NONE" else rz1) - 0.02
            nsec = max(2, int(round((top_ - rz0) / 0.56)))
            sh_ = (top_ - rz0) / nsec
            for k in range(1, nsec):
                z = rz0 + k * sh_
                bar((lx0 + 0.02, z), (lx1 - 0.02, z), 0.035, 0.03, gy - 0.004, DOOR)
            if int(s["panels"]) > 0:
                npc = max(1, int(round((lx1 - lx0) / 0.95)))
                pw_ = (lx1 - lx0 - 0.2) / npc - 0.12
                if pw_ > 0.2:
                    for k in range(nsec):
                        for c in range(npc):
                            cx_ = lx0 + 0.1 + ((lx1 - lx0 - 0.2) / npc) * (c + 0.5)
                            box(cx_, gy - 0.012, rz0 + (k + 0.5) * sh_, pw_, 0.024, sh_ - 0.14, DOOR)
        elif ltype == "ROLLER":
            top_ = (ztop_panels if lite != "NONE" else rz1) - 0.02
            nsl = max(4, min(40, int((top_ - rz0) / 0.1)))
            for k in range(nsl):
                z = rz0 + 0.04 + k * (top_ - rz0 - 0.08) / max(nsl - 1, 1)
                bar((lx0 + 0.02, z), (lx1 - 0.02, z), 0.04, 0.02, gy - 0.004, DOOR)
            box((lx0 + lx1) / 2, gy - 0.05, rz1 + 0.12, lx1 - lx0 + 0.1, 0.2, 0.24, DOOR)
        elif ltype in ("SLATS", "LOUVER"):
            step = 0.13 if ltype == "SLATS" else 0.1
            nsl = max(2, min(24, int(((ztop_panels if lite != "NONE" else rz1) - rz0) / step)))
            zlim = (ztop_panels if lite != "NONE" else rz1 - 0.04)
            for k in range(nsl):
                z = rz0 + 0.06 + k * (zlim - rz0 - 0.1) / max(nsl - 1, 1)
                ts = clip_line(region, (lx0 - 1.0, z), (1.0, 0.0))
                if ts:
                    a0 = max(lx0 + 0.04, lx0 - 1.0 + ts[0])
                    a1 = min(lx1 - 0.04, lx0 - 1.0 + ts[1])
                    if a1 - a0 > 0.1:
                        bar((a0, z), (a1, z), 0.035, 0.03 if ltype == "LOUVER" else 0.02, gy - 0.004, (DOOR if GAR else TRIM) if ltype == "SLATS" else DOOR)
        # manija
        if s["handle"]:
            hx = (lx0 + 0.18) if (leaves == 2 and li == 1) else ((lx1 - 0.18) if leaves == 1 else (lx1 - 0.18))
            box(hx, gy - 0.03, rz0 + min(1.0, lh * 0.45), 0.035, 0.06, 0.14, TRIM)


def poly_x_at(poly, z):
    """Extremos x de un polígono convexo a la altura z."""
    xs = []
    n = len(poly)
    for i in range(n):
        a, b = poly[i], poly[(i + 1) % n]
        if (a[1] <= z < b[1]) or (b[1] <= z < a[1]):
            xs.append(a[0] + (z - a[1]) / (b[1] - a[1]) * (b[0] - a[0]))
    if len(xs) < 2:
        x0, x1, _, _ = poly_bounds(poly)
        return x0, x1
    return min(xs), max(xs)


# ---- balcones ---------------------------------------------------------------------
def balcony_plan(front, hw, bd, seg=4):
    """Planta del balcón (x a lo largo de la pared, t hacia afuera), antihoraria.
    El último lado (de vuelta al primer punto) es el que toca la pared."""
    if front == "CHAMFER":
        c = max(0.1, min(bd * 0.6, hw * 0.5))
        return [(hw, 0.0), (hw, bd - c), (hw - c, bd), (-hw + c, bd), (-hw, bd - c), (-hw, 0.0)]
    if front == "ROUNDED":
        r = max(0.1, min(bd * 0.85, hw * 0.45))
        pts = [(hw, 0.0), (hw, bd - r)]
        for i in range(1, seg):
            a = (math.pi / 2) * i / seg
            pts.append((hw - r + r * math.cos(a), bd - r + r * math.sin(a)))
        pts.append((hw - r, bd))
        pts.append((-hw + r, bd))
        for i in range(1, seg):
            a = math.pi / 2 + (math.pi / 2) * i / seg
            pts.append((-hw + r + r * math.cos(a), bd - r + r * math.sin(a)))
        pts += [(-hw, bd - r), (-hw, 0.0)]
        return pts
    if front == "CURVED":
        t0 = bd * 0.35
        rt = bd - t0
        pts = [(hw, 0.0)]
        n = seg * 2
        for i in range(0, n + 1):
            a = math.pi * i / n
            pts.append((hw * math.cos(a), t0 + rt * math.sin(a)))
        pts.append((-hw, 0.0))
        return pts
    return [(hw, 0.0), (hw, bd), (-hw, bd), (-hw, 0.0)]


def _bal_beam(face, a, b, hwid, hvert, mat):
    """Viga de sección rectangular entre dos puntos 3D locales (ancho horizontal, alto vertical)."""
    A, B = Vector(a), Vector(b)
    d = B - A
    ln = d.length
    if ln < 0.02:
        return
    d /= ln
    q1 = d.cross(Vector((0, 0, 1)))
    if q1.length < 0.05:
        q1 = Vector((1, 0, 0))
    q1.normalize()
    q2 = q1.cross(d).normalized()
    c = [(sx, sy) for sx, sy in ((-1, -1), (1, -1), (1, 1), (-1, 1))]
    ra = [tuple(A + q1 * (sx * hwid) + q2 * (sy * hvert)) for sx, sy in c]
    rb = [tuple(B + q1 * (sx * hwid) + q2 * (sy * hvert)) for sx, sy in c]
    for i in range(4):
        j = (i + 1) % 4
        mid = (q1 * (c[i][0] + c[j][0]) + q2 * (c[i][1] + c[j][1]))
        face([ra[i], ra[j], rb[j], rb[i]], mat, tuple(mid))


def _bal_rod(face, a, b, r, mat):
    """Barra fina de sección triangular y sin tapas (pocos polígonos)."""
    A, B = Vector(a), Vector(b)
    d = B - A
    ln = d.length
    if ln < 0.02:
        return
    d /= ln
    q1 = d.cross(Vector((0, 0, 1)))
    if q1.length < 0.05:
        q1 = Vector((1, 0, 0))
    q1.normalize()
    q2 = q1.cross(d).normalized()
    ang = (math.pi / 2, math.pi / 2 + 2 * math.pi / 3, math.pi / 2 + 4 * math.pi / 3)
    cs = [(q1 * (r * 1.2 * math.cos(t)) + q2 * (r * 1.2 * math.sin(t))) for t in ang]
    for i in range(3):
        j = (i + 1) % 3
        face([tuple(A + cs[i]), tuple(A + cs[j]), tuple(B + cs[j]), tuple(B + cs[i])], mat, tuple(cs[i] + cs[j]))


def _bal_loggia(bm, s, o, u, n, a_mid, zb, bd, bw, rail, ww, p, lod):
    """Loggia: baranda al ras de la fachada y cerramiento vidriado al fondo."""
    # Loggia: el hueco ya está hundido en la pared (lo corta build_wall). Acá: baranda al ras de la fachada
    # (la misma que un balcón, con la losa pegada a la pared) y el cerramiento vidriado al fondo.
    fhl = (p or {}).get("floor_h", 3.0)
    st2 = dict(s, bal_type="SLAB", bal_front="RECT", bal_sides=False, bal_support="NONE", bal_roof="NONE",
               bal_depth=0.14, bal_slab_t=min(s["bal_slab_t"], 0.14))
    insert_balcony(bm, st2, o, u, n, a_mid, zb, 0.14, bw, rail, ww, p, lod, inset=0.12)
    if lod >= 1:   # LOD1: solo la baranda al ras (el vidrio del fondo ya lo da el hueco)
        return
    base0 = o + u * a_mid + Vector((0, 0, zb))
    P0, face0, poly_face0, box0, bar0, ring0, FRONT0 = style_tools(bm, base0, u, n)
    wg, hg = bw - 0.14, fhl - 0.3 - 0.06
    yb = bd - 0.08
    poly_face0([(-wg / 2, 0.02), (wg / 2, 0.02), (wg / 2, hg), (-wg / 2, hg)], yb, MAT["glass"])
    for xx in (-wg / 2, wg / 2):
        bar0((xx, 0.02), (xx, hg), 0.07, 0.07, yb, MAT["trim"])
    for zz in (0.055, hg - 0.035):
        bar0((-wg / 2, zz), (wg / 2, zz), 0.07, 0.07, yb, MAT["trim"])
    ncol = max(1, int(round(wg / 1.1)))
    for k in range(1, ncol):
        xx = -wg / 2 + wg * k / ncol
        bar0((xx, 0.02), (xx, hg), 0.045, 0.06, yb, MAT["trim"])
    return


def _bal_supports(s, plan, m, hw, bw, bd, st_t, box, beam, sweep, TRIM, Y):
    """Apoyos bajo la losa: ménsulas, soportes diagonales o viga perimetral."""
    sup = s["bal_support"]
    xs_sup = [-hw + 0.2, hw - 0.2] if bw < 2.8 else [-hw + 0.2, 0.0, hw - 0.2]
    if sup == "CORBELS":
        for x in xs_sup:
            for zz, sz in ((-st_t - 0.12, 0.24), (-st_t - 0.3, 0.12)):
                tt = min(bd * 0.7, 0.5) * (0.6 if sz < 0.2 else 1.0)
                box(x, -tt / 2, zz, 0.14, tt, sz, TRIM)
    elif sup == "BRACKETS":
        for x in xs_sup:
            tt = bd * 0.9
            beam((x, 0.0, -0.9), (x, Y(tt), -st_t), 0.025, 0.025, TRIM)
            beam((x, 0.0, -st_t - 0.02), (x, Y(tt), -st_t - 0.02), 0.025, 0.02, TRIM)
    elif sup == "BEAM":
        sweep([q for q in offset_poly([(q[0], q[1]) for q in plan], -0.05)][:m], 0.05, 0.12, -st_t - 0.12, TRIM)


def _bal_roof(s, plan, bd, fh, face, rod, prism, Y, ROOF, TRIM):
    """Techo del balcón: toldo inclinado o losa plana con dos postes."""
    roof = s["bal_roof"]
    zr = max(2.3, min(fh - 0.45, 3.2))
    if roof == "AWNING":
        drop = 0.3
        def zt(q):
            return zr - drop * (q[1] / max(bd, 0.1))
        face([(q[0], Y(q[1]), zt(q)) for q in plan], ROOF, (0, 0, 1))
        face([(q[0], Y(q[1]), zt(q) - 0.04) for q in plan], ROOF, (0, 0, -1))
        for sx in (-1, 1):
            cand = [q for q in plan if q[0] * sx > 0]
            q = max(cand, key=lambda q: q[1])
            rod((q[0], Y(q[1]), 0.0), (q[0], Y(q[1]), zt(q)), 0.02, TRIM)
    elif roof == "FLAT":
        prism([(q[0], q[1]) for q in plan], zr, zr + 0.1, ROOF)
        for sx in (-1, 1):
            cand = [q for q in plan if q[0] * sx > 0]
            q = max(cand, key=lambda q: q[1])
            rod((q[0], Y(q[1]), 0.0), (q[0], Y(q[1]), zr), 0.045, TRIM)


def _bal_bay(s, plan, inner, fh, lod, face, rod, prism, V3, Y, FAC, GLASS, TRIM, ROOF):
    """Mirador vidriado sobre el balcón."""
    bh = max(1.8, min(s["bal_bay_h"], fh - 0.3))
    sk = 0.9
    pl = [(q[0], q[1]) for q in inner]
    k = len(pl)
    for i in range(k - 1):
        a, b = pl[i], pl[(i + 1) % k]
        nx, nt = b[1] - a[1], -(b[0] - a[0])
        face([V3(a, 0.0), V3(b, 0.0), V3(b, sk), V3(a, sk)], FAC, (nx, -nt, 0))
        face([V3(a, sk), V3(b, sk), V3(b, bh - 0.12), V3(a, bh - 0.12)], GLASS, (nx, -nt, 0))
        L = math.hypot(b[0] - a[0], b[1] - a[1])
        km = max(0, int(L / 1.4)) if lod == 0 else 0
        for j in range(1, km + 1):
            f_ = j / (km + 1)
            x, t = a[0] + (b[0] - a[0]) * f_, a[1] + (b[1] - a[1]) * f_
            rod((x, Y(t), sk), (x, Y(t), bh - 0.12), 0.02, TRIM)
    if lod == 0:
        for i in range(k - 1):
            a = pl[i]
            rod((a[0], Y(a[1]), 0.0), (a[0], Y(a[1]), bh), 0.035, TRIM)
        rod((pl[-1][0], Y(pl[-1][1]), 0.0), (pl[-1][0], Y(pl[-1][1]), bh), 0.035, TRIM)
    prism([(q[0], q[1]) for q in plan], bh - 0.12, bh + 0.08, ROOF)


def insert_balcony(bm, st, o, u, n, a_mid, zb, bd=None, bw=None, rail=None, ww=1.3, p=None, lod=0, inset=0.0, plan_override=None):
    """Balcón de un estilo. Local: x a lo largo de la pared (centro = a_mid), y hacia adentro
    (el balcón sale hacia -y), z = 0 es el piso del balcón.
    plan_override = (planta [(x, t)] antihoraria, índices de los lados con baranda, índices de los lados contra la pared):
    el balcón corrido arma su propia planta (una franja que sigue varias paredes) y reutiliza toda la baranda."""
    s = dict(STYLE_DEFAULT)
    s.update(st)
    if plan_override:
        s["bal_type"], s["bal_support"], s["bal_roof"] = "SLAB", "NONE", "NONE"
    cs1 = bool((p or {}).get("_cs1"))
    solid_mat = MAT["facade"]
    if lod >= 1:  # LOD1: misma silueta con pocos polígonos (baranda maciza, sin apoyos ni adornos)
        if cs1 and s["bal_rail"] == "GLASS":
            pass          # CS1: el vidrio se queda como paño fino (sale con la imagen del vidrio)
        elif s["bal_rail"] not in ("NONE",):
            if cs1 and s["bal_rail"] != "SOLID":
                solid_mat = MAT["rail"]       # CS1: reja o balaustres = panel con la imagen del metal
            s["bal_rail"] = "SOLID"
        s["bal_support"], s["bal_planter"], s["bal_cap"] = "NONE", False, False
        if s["bal_roof"] == "AWNING":
            s["bal_roof"] = "FLAT"
    bd = bd if bd else s["bal_depth"]
    bw = bw if bw else ww + s["bal_extra_w"]
    if s["bal_type"] == "LOGGIA":
        _bal_loggia(bm, s, o, u, n, a_mid, zb, bd, bw, rail, ww, p, lod)
        return
    rail_h = rail if rail else s["bal_rail_h"]
    base = o + u * a_mid + Vector((0, 0, zb)) - n * inset
    P, face, poly_face, box, bar, ring, FRONT = style_tools(bm, base, u, n)
    TRIM, GLASS, ROOF, FAC = MAT["trim"], MAT["gls"], MAT["roof"], MAT["facade"]
    RAIL = MAT["rail"]
    hw = bw / 2.0
    st_t = s["bal_slab_t"]
    fh = (p or {}).get("floor_h", 3.0)
    if plan_override:
        plan = plan_override[0]
        hw = max(abs(q[0]) for q in plan)
    else:
        plan = balcony_plan(s["bal_front"], hw, bd)
    m = len(plan)
    Y = lambda t: -t  # t hacia afuera -> y local

    def V3(q, z):
        return (q[0], Y(q[1]), z)

    def prism(poly2, z0, z1, mat, sides=True, skip_back=True, skip=()):
        """Prisma vertical sobre una planta (lista de (x,t))."""
        face([V3(q, z1) for q in poly2], mat, (0, 0, 1))
        face([V3(q, z0) for q in poly2], mat, (0, 0, -1))
        if sides:
            k = len(poly2)
            for i in range(k):
                a, b = poly2[i], poly2[(i + 1) % k]
                if (skip_back and i == k - 1) or i in skip:
                    continue
                nx, nt = b[1] - a[1], -(b[0] - a[0])
                face([V3(a, z0), V3(b, z0), V3(b, z1), V3(a, z1)], mat, (nx, -nt, 0))

    def beam(a, b, hwid, hvert, mat):
        _bal_beam(face, a, b, hwid, hvert, mat)

    def rod(a, b, r, mat):
        _bal_rod(face, a, b, r, mat)

    # ---- losa ----
    prism(plan, -st_t, 0.0, TRIM, skip=set(plan_override[2]) if plan_override else ())

    # ---- camino de baranda (línea central, desplazada hacia adentro) ----
    rt = 0.04
    inner = offset_poly([(q[0], q[1]) for q in plan], -rt)
    path = []
    if plan_override:        # balcón corrido: la baranda va por los lados que se indican (frente y, si hay costados, las tapas)
        for i in plan_override[1]:
            path.append((inner[i], inner[(i + 1) % m]))
    else:
      for i in range(m - 1):  # sin el lado de la pared
        a, b = inner[i], inner[(i + 1) % m]
        am, bm_ = plan[i], plan[(i + 1) % m]
        midx, midt = (am[0] + bm_[0]) / 2, (am[1] + bm_[1]) / 2
        is_side = abs(midx) > hw * 0.7 and midt < bd * 0.6 and abs(am[0] - bm_[0]) < abs(am[1] - bm_[1]) * 1.2
        if is_side and not s["bal_sides"]:
            continue
        path.append((a, b))

    gap = max(0.07, s["bal_bar_gap"])
    cap_on = s["bal_cap"]
    rtype = s["bal_rail"]
    bar_r, post_r = s["bal_bar_r"], s["bal_post_r"]
    cap_hw, cap_hv = s["bal_cap_w"] / 2, s["bal_cap_h"] / 2
    zlo = 0.07
    zhi = (rail_h - cap_hv + 0.004) if (cap_on and rtype != "NONE") else rail_h   # el barrote llega al pasamanos

    def seg_frame(a, b):
        dx, dt = b[0] - a[0], b[1] - a[1]
        L = math.hypot(dx, dt)
        return L, (dx / L, dt / L)

    def at(a, d, sdist, z):
        return (a[0] + d[0] * sdist, Y(a[1] + d[1] * sdist), z)

    def sweep(pts, hwid, hvert, zc, mat):
        """Perfil rectangular recorrido por una línea (x, t) con esquinas a inglete: sin huecos ni solapes."""
        pts = [q for i, q in enumerate(pts) if i == 0 or math.hypot(q[0] - pts[i - 1][0], q[1] - pts[i - 1][1]) > 1e-4]
        k = len(pts)
        if k < 2:
            return
        dirs = []
        for i in range(k - 1):
            L_, d_ = seg_frame(pts[i], pts[i + 1])
            dirs.append(d_)
        rings, cents = [], []
        for i in range(k):
            d0 = dirs[i - 1] if i > 0 else dirs[0]
            d1 = dirs[i] if i < k - 1 else dirs[-1]
            n0, n1 = (-d0[1], d0[0]), (-d1[1], d1[0])
            den = max(1.0 + n0[0] * n1[0] + n0[1] * n1[1], 0.35)
            mx, mt = (n0[0] + n1[0]) / den, (n0[1] + n1[1]) / den
            P_ = pts[i]
            rings.append([(P_[0] + mx * hwid * sx, Y(P_[1] + mt * hwid * sx), zc + hvert * sz)
                          for sx, sz in ((1, 1), (-1, 1), (-1, -1), (1, -1))])
            cents.append((P_[0], Y(P_[1]), zc))
        for i in range(k - 1):
            cm = tuple((cents[i][j] + cents[i + 1][j]) / 2 for j in range(3))
            for j in range(4):
                jj = (j + 1) % 4
                quad = [rings[i][j], rings[i][jj], rings[i + 1][jj], rings[i + 1][j]]
                fc = tuple(sum(q[c] for q in quad) / 4 for c in range(3))
                face(quad, mat, tuple(fc[c] - cm[c] for c in range(3)))
        for ring, c0, c1 in ((rings[0], cents[0], cents[1]), (rings[-1], cents[-1], cents[-2])):  # tapas
            face(ring, mat, tuple(c0[c] - c1[c] for c in range(3)))

    # tramos continuos de la baranda (se parten donde se quitó un costado)
    chains = []
    for a_, b_ in path:
        if chains and math.hypot(chains[-1][-1][0] - a_[0], chains[-1][-1][1] - a_[1]) < 1e-4:
            chains[-1].append(b_)
        else:
            chains.append([a_, b_])
    seg_first = []
    for idx, (a_, b_) in enumerate(path):
        seg_first.append(idx == 0 or math.hypot(path[idx - 1][1][0] - a_[0], path[idx - 1][1][1] - a_[1]) > 1e-4)
    seg_last = [i == len(path) - 1 or seg_first[i + 1] for i in range(len(path))]
    acc = [0.0]

    def need_post(first, L, d_prev, d_cur):
        """Poste solo al inicio del tramo, en quiebres marcados o cada 1,5 m de recorrido."""
        if first:
            acc[0] = 0.0
            return True
        turn = d_prev[0] * d_cur[0] + d_prev[1] * d_cur[1]
        if turn < 0.9 or acc[0] >= 1.5:
            acc[0] = 0.0
            return True
        return False

    def place_posts(idx, a, b, d, L, r, spacing):
        dprev = seg_frame(*path[idx - 1])[1] if idx > 0 else d
        if need_post(seg_first[idx], L, dprev, d):
            rod((a[0], Y(a[1]), 0.0), (a[0], Y(a[1]), rail_h), r, RAIL)
        for j in range(1, int(L // spacing) + 1):
            if L - j * spacing > 0.3:
                x, t = a[0] + d[0] * j * spacing, a[1] + d[1] * j * spacing
                rod((x, Y(t), 0.0), (x, Y(t), rail_h), r, RAIL)
        acc[0] += L
        if seg_last[idx]:
            rod((b[0], Y(b[1]), 0.0), (b[0], Y(b[1]), rail_h), r, RAIL)

    for idx, (a, b) in enumerate(path):
        L, d = seg_frame(a, b)
        if L < 0.1:
            continue
        if rtype == "BALUSTERS":
            k = max(1, int(round(L / gap)))
            for i in range(0 if seg_first[idx] else 1, k + 1):
                sd = L * i / k
                x, t = a[0] + d[0] * sd, a[1] + d[1] * sd
                edge = (i == k and seg_last[idx]) or (i == 0 and seg_first[idx])
                rod((x, Y(t), zlo), (x, Y(t), zhi), post_r if edge else bar_r, RAIL)
        elif rtype == "RODS":
            nr = 4
            for i in range(nr):
                z = 0.12 + (rail_h - 0.2) * (i + 0.5) / nr
                rod((a[0], Y(a[1]), z), (b[0], Y(b[1]), z), 0.012, RAIL)
            place_posts(idx, a, b, d, L, 0.025, 1.2)
        elif rtype == "GLASS":
            place_posts(idx, a, b, d, L, 0.022, 1.5)
        elif rtype == "LATTICE":
            sp = max(0.22, gap * 2)
            hh = zhi - zlo
            kmin, kmax = int(math.floor(-hh / sp)), int(math.ceil(L / sp))
            for kk in range(kmin, kmax + 1):
                t0 = max(0.0, -kk * sp)
                t1 = min(hh, L - kk * sp)
                if t1 - t0 > 0.06:
                    s0, s1 = kk * sp + t0, kk * sp + t1
                    rod(at(a, d, s0, zlo + t0), at(a, d, s1, zlo + t1), 0.011, RAIL)
                    rod(at(a, d, s0, zhi - t0), at(a, d, s1, zhi - t1), 0.011, RAIL)
            place_posts(idx, a, b, d, L, 0.025, 2.0)
        elif rtype == "WROUGHT":
            k = max(1, int(round(L / (gap * 1.5))))
            for i in range(0 if seg_first[idx] else 1, k + 1):
                sd = L * i / k
                x, t = a[0] + d[0] * sd, a[1] + d[1] * sd
                edge = (i == k and seg_last[idx]) or (i == 0 and seg_first[idx])
                rod((x, Y(t), zlo), (x, Y(t), zhi), post_r * 0.85 if edge else bar_r * 0.9, RAIL)
            for i in range(k):  # rombo decorativo entre balaustres
                sm = L * (i + 0.5) / k
                cx, ct = a[0] + d[0] * sm, a[1] + d[1] * sm
                zc, rr = rail_h * 0.55, min(0.07, L / k * 0.35)
                pts = [at((cx, ct), d, -rr, zc), at((cx, ct), d, 0, zc + rr),
                       at((cx, ct), d, rr, zc), at((cx, ct), d, 0, zc - rr)]
                for j in range(4):
                    rod(pts[j], pts[(j + 1) % 4], 0.009, RAIL)

    # pasamanos, travesaños y paños continuos, con las esquinas a inglete
    for ch in chains:
        if rtype == "SOLID":
            # con pasamanos, el antepecho llega hasta su base: si los dos terminaran a la misma altura, sus tapas coincidirían (z-fighting)
            ph = rail_h - 2 * cap_hv if cap_on else rail_h
            sweep(ch, 0.06, ph / 2, ph / 2, solid_mat)
        elif rtype == "GLASS":
            sweep(ch, 0.012, (zhi - zlo) / 2, (zlo + zhi) / 2, GLASS)
        if rtype in ("BALUSTERS", "GLASS", "LATTICE"):
            sweep(ch, 0.03, 0.03, 0.04, RAIL)
        elif rtype == "WROUGHT":
            sweep(ch, 0.025, 0.025, 0.045, RAIL)
            sweep(ch, 0.012, 0.012, rail_h * 0.5, RAIL)
        if rtype != "NONE" and cap_on:
            sweep(ch, max(cap_hw, 0.075) if rtype == "SOLID" else cap_hw, cap_hv, rail_h - cap_hv, RAIL)   # sobre el antepecho, sobresale un poco
        if s["bal_planter"] and rtype != "NONE":
            ph = s["bal_planter_h"]
            sweep(ch, 0.09, ph / 2, ph / 2, TRIM)

    # ---- apoyos bajo la losa ----
    _bal_supports(s, plan, m, hw, bw, bd, st_t, box, beam, sweep, TRIM, Y)

    # ---- techo ----
    _bal_roof(s, plan, bd, fh, face, rod, prism, Y, ROOF, TRIM)

    # ---- mirador vidriado ----
    if s["bal_type"] == "BAY":
        _bal_bay(s, plan, inner, fh, lod, face, rod, prism, V3, Y, FAC, GLASS, TRIM, ROOF)


def insert_balcony_run(bm, st, pts, normals, zb, bd, rail, p, lod):
    """Balcón corrido: una sola losa y baranda que siguen un recorrido sobre la pared.
    pts = puntos (x, y) del recorrido en el plano de la pared, en el sentido de z x n; normals = normal exterior de cada tramo."""
    k = len(pts) - 1
    if k < 1:
        return
    n0 = Vector((normals[0].x, normals[0].y, 0.0)).normalized()
    u0 = Vector((0, 0, 1)).cross(n0).normalized()
    P0 = Vector((pts[0][0], pts[0][1], 0.0))

    def loc(q):
        d = Vector((q[0], q[1], 0.0)) - P0
        return (d.dot(u0), d.dot(n0))
    W, O = [], []
    for i, q in enumerate(pts):
        if i == 0:
            m = normals[0]
        elif i == k:
            m = normals[-1]
        else:
            m = _miter(normals[i - 1], normals[i])
        W.append(loc(q))
        O.append(loc((q[0] + m.x * bd, q[1] + m.y * bd)))
    # planta antihoraria: extremo final, frente (de atrás hacia adelante) y vuelta por la pared
    plan = [W[k]] + [O[k - j] for j in range(k + 1)] + [W[j] for j in range(k)]
    m_ = len(plan)
    sides = list(range(1, k + 1))
    if st.get("bal_sides", True):
        sides = [0] + sides + [k + 1]
    wall_edges = list(range(k + 2, m_))
    s2 = dict(st)
    if lod >= 2:
        s2["bal_rail"] = "NONE"
    insert_balcony(bm, s2, P0, u0, n0, 0.0, zb, bd, 1.0, rail, 1.0, p, lod, plan_override=(plan, sides, wall_edges))


# ----------------------------------------------------------------------------
# Biblioteca de estilos: presets incluidos + los tuyos (carpeta de usuario de Blender, JSON)
# ----------------------------------------------------------------------------
STYLE_ID_NONE = 1      # "sin estilo" en una pared
STYLE_ID_GLOBAL = 0    # "usar el estilo general"


# Versión de Blender con la que se prueba el addon (las pruebas automáticas corren acá); "blender" en bl_info es el mínimo que acepta
TESTED_BLENDER = (5, 2, 0)


def ui_note(layout, text, icon="NONE"):
    """Texto largo en un panel: se parte en renglones según el ancho de la región (Blender no ajusta las etiquetas)."""
    try:
        width = bpy.context.region.width / max(0.5, bpy.context.preferences.system.ui_scale)
    except Exception:
        width = 320
    chars = max(18, int((width - 56) / 7.0))
    col = layout.column(align=True)
    col.scale_y = 0.85
    first = True
    for para in str(text).split("\n"):
        for line in (textwrap.wrap(para, chars) or [""]):
            if first and icon != "NONE":
                col.label(text=line, icon=icon)
            else:
                col.label(text=line)
            first = False


def style_id(name):
    return (zlib.crc32(name.encode("utf-8")) & 0x7FFFFFFF) | 0x10000  # nunca 0 ni 1


def builtin_list():
    """Presets incluidos: [(nombre, ajustes)]."""
    L = []

    def W(name, **kw):
        L.append((name, dict(kind="window", **kw)))

    def D(name, **kw):
        L.append((name, dict(kind="door", **kw)))

    def B(name, **kw):
        L.append((name, dict(kind="balcony", **kw)))

    def C(name, **kw):
        L.append((name, dict(kind="curtain", **kw)))

    # --- muros cortina ---
    C("Cortina estándar")
    C("Cortina fina", cw_pt=0.04, cw_pd=0.05, cw_mw=1.2, cw_band=0.35)
    C("Cortina de perfiles gruesos", cw_pt=0.1, cw_pd=0.14, cw_prof=1, cw_mw=1.8)
    C("Vidrio estructural", cw_prof=3, cw_mw=2.0, cw_band=0.6)
    C("Franjas horizontales", cw_mw=4.0, cw_pt=0.03, cw_pd=0.05, cw_ht=0.1, cw_hd=0.16, cw_band=0.6, cw_prof=1)
    C("Torre vertical con aletas", cw_mw=0.9, cw_pt=0.05, cw_finn=1, cw_find=0.45, cw_fint=0.08, cw_band=0.3)
    C("Cortina con zócalo y cornisa", cw_baseh=0.9, cw_basemat=3, cw_caph=0.6, cw_capo=0.35, cw_capmat=1, cw_mw=1.6)
    C("Módulos alternados", cw_pat=1, cw_patk=0.5, cw_mw=1.4)
    C("Paño lleno cada tres", cw_pat=2, cw_patn=3, cw_mw=1.3, cw_bmat=3)
    C("Oficinas con acceso", cw_doorw=2.4, cw_baseh=0.3, cw_cornw=0.25, cw_mw=1.5, cw_mmat=1)
    C("Esquinas marcadas", cw_cornw=0.4, cw_prof=2, cw_pt=0.08, cw_pd=0.1, cw_mmat=1)
    C("Perfiles redondos", cw_prof=2, cw_pt=0.07, cw_pd=0.12, cw_ht=0.07, cw_hd=0.12, cw_mw=1.6)
    C("Banda ancha opaca", cw_band=0.9, cw_bmat=2, cw_mw=2.2)
    C("Cortina con márgenes", cw_edge=1.2, cw_bmat=2, cw_mw=1.7)

    # --- ventanas rectangulares ---
    W("Ventana lisa", frame_w=0.04)
    W("Ventana 2 hojas", cols=2)
    W("Ventana 3 hojas", cols=3)
    W("Ventana 2x2 cuarterones", cols=2, rows=2)
    W("Ventana 3x3 cuarterones", cols=3, rows=3)
    W("Ventana 2x3 cuarterones", cols=2, rows=3)
    W("Ventana 4x2 cuarterones", cols=4, rows=2)
    W("Ventana angosta alta", rows=4, frame_w=0.05)
    W("Ventana tira horizontal", cols=6, frame_w=0.04, casing_w=0.05)
    W("Ventana travesaño alto", cols=2, transom=0.72)
    W("Ventana travesaño 3 hojas", cols=3, transom=0.75)
    W("Ventana travesaño y cuarterones", cols=2, rows=2, transom=0.72)
    W("Ventana marco grueso", cols=2, frame_w=0.12, frame_d=0.08)
    W("Ventana celosía de rombos", lattice=3)
    W("Ventana celosía fina", lattice=5, bar_w=0.025)
    W("Ventana con moldura", casing_w=0.1, cols=2)
    W("Ventana moldura y repisa", casing_w=0.1, sill="SIMPLE", cols=2)
    W("Ventana moldura y dintel", casing_w=0.1, head="LINTEL", sill="SIMPLE", cols=2, rows=2)
    W("Ventana cornisa y repisa", casing_w=0.12, head="CORNICE", sill="SIMPLE", cols=2, rows=2)
    W("Ventana con frontón", casing_w=0.1, head="PEDIMENT", sill="SIMPLE", cols=2, rows=2)
    W("Ventana frontón y pilastras", casing_w=0.1, head="PEDIMENT", sill="SIMPLE", pilasters=True, cols=2, rows=3)
    W("Ventana con ménsulas", casing_w=0.1, head="LINTEL", sill="CORBEL", cols=2, rows=2)
    W("Ventana con clave", casing_w=0.1, head="KEYSTONE", sill="SIMPLE", cols=2, rows=2)
    W("Ventana persianas abiertas", cols=2, rows=2, casing_w=0.06, sill="SIMPLE", shutters="OPEN")
    W("Ventana persianas cerradas", shutters="CLOSED", casing_w=0.06, sill="SIMPLE")
    W("Ventana postigos lisos", cols=2, shutters="OPEN", louvers=0, casing_w=0.06)
    W("Ventana con balconcito", cols=2, rows=2, sill="BALCONETTE")
    W("Ventana balconcito y persianas", cols=2, sill="BALCONETTE", shutters="OPEN", casing_w=0.06)
    W("Ventana clásica completa", cols=2, rows=3, casing_w=0.12, head="PEDIMENT", sill="CORBEL", pilasters=True)
    # --- arcos ---
    W("Ventana arco medio punto", shape="ARCH", arch_rise=0.5)
    W("Ventana arco 2 hojas", shape="ARCH", cols=2, transom=0.6)
    W("Ventana arco con travesaño", shape="ARCH", cols=2, rows=2, transom=0.55)
    W("Ventana arco rebajado", shape="ARCH", arch_rise=0.22, cols=2)
    W("Ventana arco rebajado y clave", shape="ARCH", arch_rise=0.22, casing_w=0.1, head="KEYSTONE", sill="SIMPLE")
    W("Ventana arco moldura y clave", shape="ARCH", casing_w=0.1, head="KEYSTONE", sill="SIMPLE", cols=2)
    W("Ventana arco celosía", shape="ARCH", lattice=3)
    W("Ventana arco cuarterones", shape="ARCH", cols=3, rows=2, transom=0.55, casing_w=0.08)
    W("Ventana arco persianas", shape="ARCH", cols=2, shutters="OPEN", casing_w=0.06, sill="SIMPLE")
    W("Ventana ojival", shape="POINTED", arch_rise=0.8)
    W("Ventana ojival 2 hojas", shape="POINTED", arch_rise=0.8, cols=2)
    W("Ventana ojival con moldura", shape="POINTED", arch_rise=0.8, casing_w=0.1, sill="SIMPLE")
    W("Ventana ojival celosía", shape="POINTED", arch_rise=0.8, lattice=3)
    W("Ventana gótica alta", shape="POINTED", arch_rise=1.1, cols=2, rows=3, casing_w=0.1)
    # --- formas especiales ---
    W("Ventana circular", shape="CIRCLE", frame_w=0.08)
    W("Ventana circular con cruz", shape="CIRCLE", cols=2, rows=2, frame_w=0.08)
    W("Ventana circular con moldura", shape="CIRCLE", casing_w=0.1, frame_w=0.08)
    W("Ventana circular celosía", shape="CIRCLE", lattice=4, frame_w=0.08)
    W("Ventana ovalada", shape="OVAL", frame_w=0.07)
    W("Ventana ovalada con moldura", shape="OVAL", casing_w=0.1, frame_w=0.07)
    W("Ventana ovalada con barras", shape="OVAL", rows=2, frame_w=0.07)
    W("Ventana octogonal", shape="OCTAGON", frame_w=0.07)
    W("Ventana octogonal con moldura", shape="OCTAGON", casing_w=0.1, frame_w=0.07)
    W("Ventana octogonal con cruz", shape="OCTAGON", cols=2, rows=2, frame_w=0.07)
    W("Ventana rombo", shape="DIAMOND", frame_w=0.07)
    W("Ventana rombo celosía", shape="DIAMOND", lattice=3, frame_w=0.07)
    W("Ventana rombo con moldura", shape="DIAMOND", casing_w=0.1, frame_w=0.07)

    # --- puertas ---
    D("Puerta lisa", leaf_type="FLUSH")
    D("Puerta 2 cuarterones", leaf_type="PANELS", panels=2)
    D("Puerta 3 cuarterones", leaf_type="PANELS", panels=3)
    D("Puerta 4 cuarterones", leaf_type="PANELS", panels=4)
    D("Puerta vidrio arriba", leaf_type="PANELS", panels=1, lite="TOP", glass_frac=0.35)
    D("Puerta vidrio mitad", leaf_type="PANELS", panels=1, lite="TOP", glass_frac=0.5)
    D("Puerta vidrio y cuarterones", leaf_type="PANELS", panels=2, lite="TOP", glass_frac=0.4)
    D("Puerta vidrio completo", leaf_type="FLUSH", lite="FULL", cols=1, rows=3, frame_w=0.07)
    D("Puerta vidrio completo con divisiones", leaf_type="FLUSH", lite="FULL", cols=2, rows=4, frame_w=0.07)
    D("Puerta óvalo", leaf_type="PANELS", panels=2, lite="OVAL")
    D("Puerta ojo de buey", leaf_type="PANELS", panels=2, lite="ROUND")
    D("Puerta ranuras verticales", leaf_type="FLUSH", lite="SLOTS", lite_n=3)
    D("Puerta ranuras dobles", leaf_type="FLUSH", lite="SLOTS", lite_n=2)
    D("Puerta ventanitas cuadradas", leaf_type="PANELS", panels=1, lite="GRID", lite_n=3)
    D("Puerta ventanita", leaf_type="FLUSH", lite="GRID", lite_n=1)
    D("Puerta de tablones", leaf_type="PLANK")
    D("Puerta tablones con cruz", leaf_type="BRACED")
    D("Puerta tablones con cruz y arco", leaf_type="BRACED", shape="ARCH", arch_rise=0.4, frame_w=0.08)
    D("Puerta rústica con ventanita", leaf_type="BRACED", lite="GRID", lite_n=1, shape="ARCH", arch_rise=0.35)
    D("Puerta de persiana", leaf_type="LOUVER")
    D("Portón de tiras", leaf_type="SLATS", frame_w=0.05, handle=False)
    D("Portón con vidrios arriba", leaf_type="SLATS", lite="TOP", glass_frac=0.18, frame_w=0.05, handle=False)
    D("Puerta doble lisa", leaves=2, leaf_type="FLUSH")
    D("Puerta doble cuarterones", leaves=2, leaf_type="PANELS", panels=2)
    D("Puerta doble vidrio arriba", leaves=2, leaf_type="PANELS", panels=1, lite="TOP", glass_frac=0.45)
    D("Puerta doble vidrio completo", leaves=2, leaf_type="FLUSH", lite="FULL", rows=3, frame_w=0.07)
    D("Puerta doble de persiana", leaves=2, leaf_type="LOUVER")
    D("Puerta doble de tablones", leaves=2, leaf_type="PLANK")
    D("Puerta con montante", leaf_type="PANELS", panels=2, transom=0.8, cols=3)
    D("Puerta vidrio y montante", leaf_type="PANELS", panels=1, lite="TOP", glass_frac=0.4, transom=0.82, cols=4)
    D("Puerta doble con montante", leaves=2, leaf_type="PANELS", panels=2, transom=0.8, cols=4)
    D("Puerta con laterales y montante", leaf_type="PANELS", panels=2, lite="TOP", glass_frac=0.4,
      sidelights=0.2, transom=0.82, cols=3)
    D("Puerta doble con laterales", leaves=2, leaf_type="PANELS", panels=2, sidelights=0.18, transom=0.82, cols=5)
    D("Puerta arco medio punto", shape="ARCH", arch_rise=0.5, leaf_type="PANELS", panels=2)
    D("Puerta arco doble hoja", shape="ARCH", arch_rise=0.5, leaves=2, leaf_type="PANELS", panels=2)
    D("Puerta arco con montante", shape="ARCH", arch_rise=0.5, leaves=2, leaf_type="PANELS", panels=2,
      transom=0.72, cols=4, rows=2, frame_w=0.07)
    D("Puerta arco con vidrio", shape="ARCH", arch_rise=0.5, leaf_type="PANELS", panels=1, lite="TOP", glass_frac=0.45)
    D("Puerta arco moldura y clave", shape="ARCH", arch_rise=0.5, casing_w=0.12, head="KEYSTONE", leaf_type="PANELS", panels=2)
    D("Puerta arco rebajado", shape="ARCH", arch_rise=0.22, leaf_type="PANELS", panels=2, casing_w=0.08)
    D("Puerta ojival", shape="POINTED", arch_rise=0.7, leaf_type="PANELS", panels=2, frame_w=0.08)
    D("Puerta ojival de tablones", shape="POINTED", arch_rise=0.7, leaf_type="PLANK", frame_w=0.08)
    D("Puerta ojival con vidrio", shape="POINTED", arch_rise=0.7, leaf_type="PANELS", panels=1, lite="TOP", glass_frac=0.4)
    D("Puerta con moldura", casing_w=0.12, leaf_type="PANELS", panels=2)
    D("Puerta moldura y dintel", casing_w=0.12, head="LINTEL", leaf_type="PANELS", panels=3)
    D("Puerta con frontón", casing_w=0.1, head="PEDIMENT", leaf_type="PANELS", panels=2)
    D("Puerta frontón y pilastras", casing_w=0.1, head="PEDIMENT", pilasters=True, leaf_type="PANELS", panels=2,
      lite="TOP", glass_frac=0.35)
    D("Puerta doble frontón pilastras y montante", leaves=2, casing_w=0.1, head="PEDIMENT", pilasters=True,
      leaf_type="PANELS", panels=2, transom=0.8, cols=4)
    D("Puerta cornisa", casing_w=0.1, head="CORNICE", leaf_type="PANELS", panels=2)
    # --- portones de garaje (medidas propias) ---
    G = dict(garage=True, handle=False, frame_w=0.05, casing_w=0.06)
    D("Garaje seccional liso", leaf_type="SECTIONAL", panels=0, size_w=2.7, size_h=2.2, **G)
    D("Garaje seccional doble", leaf_type="SECTIONAL", panels=0, size_w=4.9, size_h=2.2, **G)
    D("Garaje seccional cuarterones", leaf_type="SECTIONAL", panels=1, size_w=2.7, size_h=2.2, **G)
    D("Garaje cuarterones doble", leaf_type="SECTIONAL", panels=1, size_w=4.9, size_h=2.2, **G)
    D("Garaje seccional con ventanitas", leaf_type="SECTIONAL", panels=0, lite="TOP", glass_frac=0.16, cols=4,
      size_w=2.7, size_h=2.2, **G)
    D("Garaje doble con ventanitas", leaf_type="SECTIONAL", panels=0, lite="TOP", glass_frac=0.16, cols=8,
      size_w=4.9, size_h=2.2, **G)
    D("Garaje cuarterones y ventanitas", leaf_type="SECTIONAL", panels=1, lite="TOP", glass_frac=0.16, cols=4,
      size_w=2.7, size_h=2.2, **G)
    D("Garaje enrollable", leaf_type="ROLLER", size_w=2.7, size_h=2.3, **G)
    D("Garaje enrollable doble", leaf_type="ROLLER", size_w=4.9, size_h=2.3, **G)
    D("Garaje portón de tablones", leaf_type="PLANK", leaves=2, size_w=3.0, size_h=2.3, **G)
    D("Garaje portón rústico con cruz", leaf_type="BRACED", leaves=2, size_w=3.0, size_h=2.3, **G)
    D("Garaje portón de tablones arco", leaf_type="BRACED", leaves=2, shape="ARCH", arch_rise=0.25, size_w=3.2,
      size_h=2.5, **G)
    D("Garaje portón vidrio y aluminio", leaf_type="FLUSH", lite="FULL", rows=4, cols=2, size_w=2.7,
      size_h=2.2, **G)
    D("Garaje portón liso doble hoja", leaf_type="FLUSH", leaves=2, size_w=3.0, size_h=2.2, **G)
    # --- balcones ---
    B("Balcón losa simple", bal_rail="NONE", bal_cap=False)
    B("Balcón baranda maciza", bal_rail="SOLID")
    B("Balcón antepecho con remate", bal_rail="SOLID", bal_support="BEAM")
    B("Balcón balaustres", bal_rail="BALUSTERS")
    B("Balcón balaustres finos", bal_rail="BALUSTERS", bal_bar_gap=0.09)
    B("Balcón balaustres y ménsulas", bal_rail="BALUSTERS", bal_support="CORBELS")
    B("Balcón balaustres y viga", bal_rail="BALUSTERS", bal_support="BEAM", bal_slab_t=0.14)
    B("Balcón barras horizontales", bal_rail="RODS")
    B("Balcón barras y escuadras", bal_rail="RODS", bal_support="BRACKETS")
    B("Balcón moderno vidrio", bal_rail="GLASS", bal_depth=1.5, bal_slab_t=0.14, bal_cap=False)
    B("Balcón vidrio y pasamanos", bal_rail="GLASS", bal_depth=1.5)
    B("Balcón vidrio ancho", bal_rail="GLASS", bal_depth=1.8, bal_extra_w=1.6, bal_slab_t=0.14)
    B("Balcón celosía", bal_rail="LATTICE")
    B("Balcón celosía y ménsulas", bal_rail="LATTICE", bal_support="CORBELS")
    B("Balcón herrería", bal_rail="WROUGHT")
    B("Balcón herrería y ménsulas", bal_rail="WROUGHT", bal_support="CORBELS")
    B("Balcón herrería y escuadras", bal_rail="WROUGHT", bal_support="BRACKETS", bal_depth=0.9)
    B("Balcón francés", bal_rail="WROUGHT", bal_depth=0.45, bal_extra_w=0.3, bal_slab_t=0.1)
    B("Balcón francés balaustres", bal_rail="BALUSTERS", bal_depth=0.45, bal_extra_w=0.3, bal_slab_t=0.1)
    B("Balcón francés vidrio", bal_rail="GLASS", bal_depth=0.4, bal_extra_w=0.2, bal_slab_t=0.1)
    B("Balcón profundo balaustres", bal_rail="BALUSTERS", bal_depth=2.0, bal_extra_w=1.4)
    B("Balcón terraza vidrio", bal_rail="GLASS", bal_depth=2.4, bal_extra_w=2.0, bal_slab_t=0.2)
    B("Balcón corrido", bal_rail="BALUSTERS", bal_depth=1.1, bal_extra_w=2.2)
    B("Corrido continuo vidrio", bal_run=True, bal_rail="GLASS", bal_depth=1.5, bal_cap=True)
    B("Corrido continuo balaustres", bal_run=True, bal_rail="BALUSTERS", bal_depth=1.3)
    B("Corrido continuo macizo", bal_run=True, bal_rail="SOLID", bal_depth=1.4)
    B("Balcón corrido vidrio", bal_rail="GLASS", bal_depth=1.3, bal_extra_w=2.4, bal_slab_t=0.14)
    B("Balcón facetado balaustres", bal_front="CHAMFER", bal_rail="BALUSTERS", bal_depth=1.3)
    B("Balcón facetado vidrio", bal_front="CHAMFER", bal_rail="GLASS", bal_depth=1.4)
    B("Balcón facetado herrería", bal_front="CHAMFER", bal_rail="WROUGHT", bal_support="CORBELS", bal_depth=1.3)
    B("Balcón redondeado vidrio", bal_front="ROUNDED", bal_rail="GLASS", bal_depth=1.6, bal_slab_t=0.14)
    B("Balcón redondeado balaustres", bal_front="ROUNDED", bal_rail="BALUSTERS", bal_depth=1.4)
    B("Balcón redondeado macizo", bal_front="ROUNDED", bal_rail="SOLID", bal_depth=1.4)
    B("Balcón curvo balaustres", bal_front="CURVED", bal_rail="BALUSTERS", bal_depth=1.5)
    B("Balcón curvo herrería", bal_front="CURVED", bal_rail="WROUGHT", bal_support="CORBELS", bal_depth=1.5)
    B("Balcón curvo vidrio", bal_front="CURVED", bal_rail="GLASS", bal_depth=1.6, bal_slab_t=0.14)
    B("Balcón con toldo", bal_rail="BALUSTERS", bal_roof="AWNING")
    B("Balcón toldo y vidrio", bal_rail="GLASS", bal_roof="AWNING", bal_depth=1.5)
    B("Balcón con techo y pilares", bal_rail="BALUSTERS", bal_roof="FLAT", bal_depth=1.5)
    B("Balcón pérgola herrería", bal_rail="WROUGHT", bal_roof="FLAT", bal_depth=1.5, bal_support="CORBELS")
    B("Balcón con maceteros", bal_rail="BALUSTERS", bal_planter=True)
    B("Balcón maceteros y vidrio", bal_rail="GLASS", bal_planter=True, bal_depth=1.5)
    B("Balcón rodillo sin laterales", bal_rail="RODS", bal_sides=False)
    B("Balcón abierto sin laterales", bal_rail="GLASS", bal_sides=False, bal_depth=1.4)
    B("Loggia vidrio", bal_type="LOGGIA", bal_rail="GLASS", bal_depth=1.5, bal_extra_w=1.4, bal_cap=True, bal_cap_h=0.05)
    B("Loggia balaustres", bal_type="LOGGIA", bal_rail="BALUSTERS", bal_depth=1.4, bal_extra_w=1.2)
    B("Loggia herrería", bal_type="LOGGIA", bal_rail="WROUGHT", bal_depth=1.4, bal_extra_w=1.2)
    B("Loggia baranda maciza", bal_type="LOGGIA", bal_rail="SOLID", bal_depth=1.6, bal_extra_w=1.6)
    B("Loggia ancha vidrio", bal_type="LOGGIA", bal_rail="GLASS", bal_depth=1.8, bal_extra_w=2.6)
    B("Loggia con macetero", bal_type="LOGGIA", bal_rail="GLASS", bal_depth=1.5, bal_extra_w=1.4, bal_planter=True)
    B("Mirador vidriado", bal_type="BAY", bal_rail="NONE", bal_depth=0.9, bal_extra_w=0.6, bal_cap=False)
    B("Mirador facetado", bal_type="BAY", bal_front="CHAMFER", bal_rail="NONE", bal_depth=1.1, bal_cap=False)
    B("Mirador curvo", bal_type="BAY", bal_front="CURVED", bal_rail="NONE", bal_depth=1.2, bal_cap=False)
    B("Mirador redondeado", bal_type="BAY", bal_front="ROUNDED", bal_rail="NONE", bal_depth=1.2, bal_cap=False)
    B("Mirador con ménsulas", bal_type="BAY", bal_front="CHAMFER", bal_rail="NONE", bal_depth=1.1, bal_support="CORBELS", bal_cap=False)
    return L


_LIB = {"names": [], "styles": {}, "ids": {}, "user": set(), "ready": False}
_ITEMS = {}


def style_dir():
    try:
        d = bpy.utils.user_resource("CONFIG", path="cs2_estilos", create=True)
    except Exception:
        return None
    if d and not _LIB.get("readme"):
        _LIB["readme"] = True
        try:
            write_readme(d)
        except Exception:
            _log_exc("style_dir")
    return d


def _read_style_file(path):
    """Devuelve [(nombre, ajustes)] de un archivo .json (un estilo o un paquete)."""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception:
        _log_exc("leer_estilo")
        return []
    out = []
    if isinstance(data, dict) and "styles" in data and isinstance(data["styles"], list):
        entries = data["styles"]
    elif isinstance(data, dict) and "style" in data:
        entries = [data]
    else:
        entries = []
    for e in entries:
        if isinstance(e, dict) and isinstance(e.get("style"), dict) and e.get("name"):
            out.append((str(e["name"]), clean_style(e["style"])))
    return out


def clean_style(st):
    """Solo claves conocidas, con el tipo correcto."""
    out = {}
    for k, v0 in STYLE_DEFAULT.items():
        if k in st:
            v = st[k]
            try:
                if isinstance(v0, bool):
                    v = bool(v)
                elif isinstance(v0, int):
                    v = int(v)
                elif isinstance(v0, float):
                    v = float(v)
                else:
                    v = str(v)
            except (TypeError, ValueError):
                continue
            out[k] = v
    return out


def lib_refresh():
    _THUMBS["icons"].clear()
    styles = {}
    user = set()
    for name, st in builtin_list():
        styles[name] = {**STYLE_DEFAULT, **st}
    d = style_dir()
    if d and os.path.isdir(d):
        for fn in sorted(os.listdir(d)):
            if fn.lower().endswith(".json"):
                for name, st in _read_style_file(os.path.join(d, fn)):
                    styles[name] = {**STYLE_DEFAULT, **st}
                    user.add(name)
    names = [n for n in styles if n not in user and styles[n]["kind"] == "window"]
    names += [n for n in styles if n not in user and styles[n]["kind"] == "door"]
    names += [n for n in styles if n not in user and styles[n]["kind"] == "balcony"]
    names += [n for n in styles if n not in user and styles[n]["kind"] == "curtain"]
    names += sorted(n for n in user)
    _LIB["names"], _LIB["styles"], _LIB["user"] = names, styles, user
    _LIB["ids"] = {style_id(n): n for n in names}
    _LIB["ready"] = True


def lib_ensure():
    if not _LIB["ready"]:
        lib_refresh()


def get_style(name):
    lib_ensure()
    return _LIB["styles"].get(name)


def style_by_id(sid):
    lib_ensure()
    name = _LIB["ids"].get(int(sid))
    return _LIB["styles"].get(name) if name else None


def style_for(p, mat, cfg=None, op=None):
    """Estilo que le toca a una abertura: el de su ranura (si se lo cambiaron a mano), el de su pared o el general; None si no hay
    (la vidriera comercial, de vidrio liso, no lleva)."""
    if mat == MAT["gls"]:
        return None
    ss = p.get("_ss")
    if ss and op is not None:
        k_ = (round(op[0], 2), round(op[2], 2))
        if k_ in ss:
            return ss[k_] or None          # False = «sin estilo»
    is_door = mat == MAT["door"]
    if cfg:
        sid = int(cfg.get("d_style" if is_door else "w_style", 0) or 0)
        if sid == STYLE_ID_NONE:
            return None
        if sid:
            st = style_by_id(sid)
            if st:
                return st
    return p.get("door_style" if is_door else "window_style")


def style_for_balcony(p, cfg=None):
    """Estilo de balcón de una pared, el general, o None (balcón simple de siempre)."""
    if cfg:
        sid = int(cfg.get("b_style", 0) or 0)
        if sid == STYLE_ID_NONE:
            return None
        if sid:
            st = style_by_id(sid)
            if st:
                return st
    return p.get("balcony_style")


def save_user_style(name, st):
    d = style_dir()
    if not d:
        raise RuntimeError("No se encontró la carpeta de usuario de Blender")
    safe = "".join(ch if (ch.isalnum() or ch in " -_") else "_" for ch in name).strip() or "estilo"
    path = os.path.join(d, safe + ".json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"app": "cs2_edificio", "version": 1, "name": name, "style": clean_style(st)}, fh,
                  ensure_ascii=False, indent=1)
    lib_refresh()
    return path


def delete_user_style(name):
    d = style_dir()
    if not d:
        return False
    done = False
    for fn in os.listdir(d):
        if fn.lower().endswith(".json"):
            path = os.path.join(d, fn)
            entries = _read_style_file(path)
            if len(entries) == 1 and entries[0][0] == name:
                os.remove(path)
                done = True
    lib_refresh()
    return done


def _items(kind, none_id, none_label, extra=None):
    lib_ensure()
    items = [(none_id, none_label, "", "X" if False else 0, 0)]
    if extra:
        items.append((extra[0], extra[1], extra[2], 0, len(items)))
    for n in _LIB["names"]:
        if kind is None or _LIB["styles"][n]["kind"] == kind:
            items.append((n, ("★ " if n in _LIB["user"] else "") + n, "", thumb_icon(n), len(items)))
    _ITEMS[(kind, none_id)] = items
    return items


def _items_window(self, context):
    return _items("window", "NONE", "Sin estilo (abertura simple o tus piezas)")


def _items_door(self, context):
    return _items("door", "NONE", "Sin estilo (abertura simple o tus piezas)")


def _items_wall_window(self, context):
    return _items("window", "__GLOBAL__", "Estilo general", ("__NONE__", "Sin estilo en esta pared", ""))


def _items_wall_door(self, context):
    return _items("door", "__GLOBAL__", "Estilo general", ("__NONE__", "Sin estilo en esta pared", ""))


def _items_balcony(self, context):
    return _items("balcony", "NONE", "Balcón simple (sin estilo)")


def _items_wall_balcony(self, context):
    return _items("balcony", "__GLOBAL__", "Estilo general", ("__NONE__", "Balcón simple en esta pared", ""))


def _items_pick(self, context):
    return _items(self.kind, "__NONE__", "—")


def _items_all(self, context):
    return _items(None, "__NONE__", "—")


def wall_style_id(identifier):
    if identifier == "__GLOBAL__" or not identifier:
        return STYLE_ID_GLOBAL
    if identifier == "__NONE__":
        return STYLE_ID_NONE
    return style_id(identifier)


def wall_style_identifier(sid):
    sid = int(sid)
    if sid == STYLE_ID_GLOBAL:
        return "__GLOBAL__"
    if sid == STYLE_ID_NONE:
        return "__NONE__"
    lib_ensure()
    return _LIB["ids"].get(sid, "__GLOBAL__")


# ----------------------------------------------------------------------------
# Fachadas: grilla de celdas, las que caen en una abertura se hunden (reveal)
# ----------------------------------------------------------------------------
def facade_faces(bm, origin, n, length, H, ops, depth, p, fid):
    n = Vector(n)
    zaxis = Vector((0, 0, 1))
    u = zaxis.cross(n)  # dirección a lo largo de la fachada
    o = Vector((origin[0], origin[1], 0.0))
    r = lambda v: round(v, 4)

    xs = sorted({r(0.0), r(length), *[r(op[0]) for op in ops], *[r(op[1]) for op in ops]})
    zs = sorted({r(0.0), r(H), *[r(op[2]) for op in ops], *[r(op[3]) for op in ops]})
    nx, nz = len(xs) - 1, len(zs) - 1

    cell_op = {}
    for k, (u0, u1, z0, z1, _m) in enumerate(ops):
        for i in range(nx):
            if xs[i] >= r(u0) - 1e-6 and xs[i + 1] <= r(u1) + 1e-6:
                for j in range(nz):
                    if zs[j] >= r(z0) - 1e-6 and zs[j + 1] <= r(z1) + 1e-6:
                        cell_op[(i, j)] = k

    cache = {}

    def V(i, j, back=False):
        key = (i, j, back)
        if key not in cache:
            pos = o + u * xs[i] + Vector((0, 0, zs[j]))
            if back:
                pos = pos - n * depth
            cache[key] = bm.verts.new(pos)
        return cache[key]

    def F(verts, mat):
        bm.faces.new(verts).material_index = mat

    for i in range(nx):
        for j in range(nz):
            k = cell_op.get((i, j))
            if k is None:
                F([V(i, j), V(i + 1, j), V(i + 1, j + 1), V(i, j + 1)], MAT["facade"])
                continue
            m = ops[k][4]
            if depth <= 0:  # LOD1: la abertura queda al ras
                F([V(i, j), V(i + 1, j), V(i + 1, j + 1), V(i, j + 1)], m)
                continue
            if pieces_for(p, m) or style_for(p, m, None, ops[k]):  # con pieza o estilo propio, el fondo del hueco es pared
                m = MAT["facade"]
            F([V(i, j, True), V(i + 1, j, True), V(i + 1, j + 1, True), V(i, j + 1, True)], m)
            if cell_op.get((i, j - 1)) != k:  # alféizar
                F([V(i, j), V(i + 1, j), V(i + 1, j, True), V(i, j, True)], MAT["facade"])
            if cell_op.get((i, j + 1)) != k:  # dintel
                F([V(i, j + 1), V(i, j + 1, True), V(i + 1, j + 1, True), V(i + 1, j + 1)], MAT["facade"])
            if cell_op.get((i - 1, j)) != k:  # jamba izquierda
                F([V(i, j), V(i, j, True), V(i, j + 1, True), V(i, j + 1)], MAT["facade"])
            if cell_op.get((i + 1, j)) != k:  # jamba derecha
                F([V(i + 1, j), V(i + 1, j + 1), V(i + 1, j + 1, True), V(i + 1, j, True)], MAT["facade"])

    if depth > 0:  # estilos o piezas propias (solo LOD0)
        for k, op in enumerate(ops):
            st = style_for(p, op[4], None, op)
            if st:
                insert_style(bm, st, o, u, n, op, depth, p)
                continue
            pool = pieces_for(p, op[4])
            if pool:
                idx = rng_for(p, "piece", fid, k).randrange(len(pool)) if p["piece_random"] else 0
                insert_piece(bm, pool[idx], o, u, n, op, p)


# ----------------------------------------------------------------------------
# Edificio completo
# ----------------------------------------------------------------------------
def build_bmesh(p, lod):
    bm = bmesh.new()
    bm.loops.layers.uv.new("UVMap")
    bm.faces.layers.int.new("is_piece")
    W, D = p["width"], p["depth"]
    fh = p["floor_h"]
    H = p["floors"] * fh
    depth = p["win_depth"] if lod == 0 else 0.0

    facades = [  # origen, normal, largo, es_frente
        ((-W / 2, -D / 2), (0, -1, 0), W, True),
        ((W / 2, -D / 2), (1, 0, 0), D, False),
        ((W / 2, D / 2), (0, 1, 0), W, False),
        ((-W / 2, D / 2), (-1, 0, 0), D, False),
    ]
    for fid, (origin, nrm, length, front) in enumerate(facades):
        ops = make_openings(p, length, front, lod, fid)
        facade_faces(bm, origin, nrm, length, H, ops, depth, p, fid)

    # --- techo ---
    V = lambda x, y, z: bm.verts.new((x, y, z))
    if p["roof"] == "flat":
        f = bm.faces.new([V(-W / 2, -D / 2, H), V(W / 2, -D / 2, H), V(W / 2, D / 2, H), V(-W / 2, D / 2, H)])
        f.material_index = MAT["roof"]
        if lod <= 1:
            add_ring(bm, W, D, 0.0, 0.25, H, H + p["parapet_h"], MAT["trim"])
            if not p["no_roof_box"]:
                cx_s, cy_s = p["roof_box_corner"]
                add_box(bm, cx_s * (W / 2 - 2.0), cy_s * (D / 2 - 2.0), H + 1.3, 3.0, 3.0, 2.6, MAT["facade"])
        if lod == 0:
            add_ring(bm, W, D, 0.15, 0.4, H - 0.3, H, MAT["trim"])
    else:  # gable con caballete a lo largo de X
        rh, oh = p["roof_height"], 0.5
        drop = oh * rh / (D / 2)
        ez = H - drop
        front = [V(-W / 2, -(D / 2 + oh), ez), V(W / 2, -(D / 2 + oh), ez),
                 V(W / 2, 0, H + rh), V(-W / 2, 0, H + rh)]
        back = [V(W / 2, D / 2 + oh, ez), V(-W / 2, D / 2 + oh, ez),
                V(-W / 2, 0, H + rh), V(W / 2, 0, H + rh)]
        for quad in (front, back):
            bm.faces.new(quad).material_index = MAT["roof"]
        bm.faces.new([V(W / 2, -D / 2, H), V(W / 2, D / 2, H), V(W / 2, 0, H + rh)]).material_index = MAT["facade"]
        bm.faces.new([V(-W / 2, D / 2, H), V(-W / 2, -D / 2, H), V(-W / 2, 0, H + rh)]).material_index = MAT["facade"]

    # --- cornisas entre pisos (solo LOD0) ---
    if lod == 0 and not p["no_string_courses"]:
        for f in range(1, p["floors"]):
            add_ring(bm, W, D, 0.04, 0.1, f * fh - 0.35, f * fh - 0.2, MAT["trim"])

    # --- balcones en el frente (solo LOD0) ---
    if lod == 0:
        ww = p["win_w"]
        cols = window_columns(W, ww, p["min_gap"])
        bd, bw = p["balcony_depth"], ww + 0.8
        for f in range(1, p["floors"]):
            for k in range(len(cols)):
                if not has_balcony(p, f, k):
                    continue
                cx = -W / 2 + cols[k] + ww / 2
                cy = -D / 2 - bd / 2
                z = f * fh
                if p.get("balcony_style"):
                    insert_balcony(bm, p["balcony_style"], Vector((-W / 2, -D / 2, 0.0)), Vector((1, 0, 0)),
                                   Vector((0, -1, 0)), cols[k] + ww / 2, z, None, None, None, ww, p)
                    continue
                add_box(bm, cx, cy, z - 0.09, bw, bd, 0.18, MAT["trim"], skip=("+y",))
                add_box(bm, cx, -D / 2 - bd + 0.04, z + 0.45, bw, 0.08, 0.9, MAT["trim"])
                for sx in (-1, 1):
                    add_box(bm, cx + sx * (bw / 2 - 0.04), cy, z + 0.45, 0.08, bd - 0.08, 0.9, MAT["trim"])

    finish_bmesh(bm, p)
    return bm


# ----------------------------------------------------------------------------
# Modo "forma": el edificio se genera a partir de una malla base que podés
# extruir y editar (paredes verticales -> fachadas, caras hacia arriba -> techo)
# ----------------------------------------------------------------------------
# Atlas UV0 (todo entre 0 y 1; cada material en su propio sector, separados por espacio vacío)
ATLAS_ORDER = ("facade", "roof", "facb", "facc", "facd", "trim", "door", "gls", "gar", "rail")
ATLAS_MARGIN = 0.012   # separación entre sectores (en UV, a cada lado; 12 px a 1K) para que el mipmap no mezcle materiales


def layout_atlas(extents, margin=ATLAS_MARGIN):
    """Empaqueta un rectángulo por material (el tamaño en metros de su pared más grande) en el 0-1.

    Todos los materiales usan la misma escala (UV por metro), la mayor que entra: así cada sector se llena con su
    pared más grande y la densidad de textura es igual en todo el edificio.
    extents = {nombre: (ancho_m, alto_m)}. Devuelve ({nombre: (u0, v0, u1, v1)}, escala)."""
    items = [(n, max(float(extents[n][0]), 1.0), max(float(extents[n][1]), 1.0))
             for n in ATLAS_ORDER if n in extents] or [("facade", 1.0, 1.0)]

    def pack(sc):
        """Estantes: de más alto a más bajo; None si no entra."""
        placed, x, y, row_h = {}, 0.0, 0.0, 0.0
        for n, w, h in sorted(items, key=lambda t: -t[2]):
            pw, ph = w * sc + 2 * margin, h * sc + 2 * margin
            if pw > 1.0 + 1e-9:
                return None
            if x + pw > 1.0 + 1e-9:
                x, y, row_h = 0.0, y + row_h, 0.0
            if y + ph > 1.0 + 1e-9:
                return None
            placed[n] = (x + margin, y + margin, x + margin + w * sc, y + margin + h * sc)
            x += pw
            row_h = max(row_h, ph)
        return placed

    lo, hi = 0.0, 1.0
    best = pack(1e-4) or {}
    for _ in range(40):
        mid = (lo + hi) / 2
        got = pack(mid)
        if got is None:
            hi = mid
        else:
            lo, best = mid, got
    return best, lo


ATLAS_DEFAULT_EXT = {"facade": (20.0, 9.0), "roof": (20.0, 20.0), "facb": (8.0, 6.0), "facc": (8.0, 6.0),
                     "facd": (8.0, 6.0), "trim": (20.0, 2.0), "door": (3.0, 3.0), "gls": (3.0, 3.0),
                     "gar": (5.0, 2.5), "rail": (8.0, 1.2)}
ATLAS, ATLAS_SCALE = layout_atlas(ATLAS_DEFAULT_EXT)   # diseño por defecto; cada edificio calcula el suyo
ATLAS_BY_MAT = {MAT["facade"]: "facade", MAT["trim"]: "trim", MAT["roof"]: "roof",
                MAT["door"]: "door", MAT["gls"]: "gls", MAT["facb"]: "facb", MAT["facc"]: "facc",
                MAT["facd"]: "facd", MAT["gar"]: "gar", MAT["rail"]: "rail"}
WIN_GRID = 5      # textura de referencia de ventanas: 25 colores (Siempre encendida, Cuarto 01-23, Siempre apagada)
SUB_MATS = (MAT["glass"], MAT["gls"])   # van a mallas aparte (_Win / _Gls), no a la principal


def window_cell(mode, key):
    """Índice (0-24) de la celda de la textura de referencia: 0 encendida, 1-23 cuartos, 24 apagada."""
    if mode == "on":
        return 0
    if mode == "off":
        return 24
    return 1 + (zlib.crc32(key.encode("utf-8")) % 23)


WALL_REGIONS = ("facade", "facb", "facc", "facd")


def _uv_turn(pts, deg):
    """Gira (grados) una lista de puntos planos. Los múltiplos de 90° son exactos (sin error de redondeo)."""
    q = round(deg / 90.0)
    if abs(deg - q * 90.0) < 1e-6:
        c, s_ = (1.0, 0.0, -1.0, 0.0)[q % 4], (0.0, 1.0, 0.0, -1.0)[q % 4]
    else:
        r = math.radians(deg)
        c, s_ = math.cos(r), math.sin(r)
    return [(x * c - y * s_, x * s_ + y * c) for x, y in pts]


def face_uv_turn(p, region, wall_quarters=0):
    """Giro total (0 a 360°) de la textura de una cara: el del material más el de su pared (en cuartos de vuelta)."""
    try:
        deg = float(((p.get("matslots") or {}).get(region) or {}).get("rot", 0.0))
    except (TypeError, ValueError):
        deg = 0.0
    if region in WALL_REGIONS:
        deg += 90.0 * int(wall_quarters)
    return deg % 360.0


def finish_bmesh(bm, p):
    """Une vértices repetidos y genera las UVs del atlas."""
    bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=1e-5)
    if p.get("_dissolve"):  # LOD1/LOD2: une las caras coplanares del mismo material (menos triángulos)
        bm.normal_update()
        bmesh.ops.dissolve_limit(bm, angle_limit=math.radians(1.0), verts=bm.verts, edges=bm.edges,
                                 use_dissolve_boundaries=False, delimit={"MATERIAL"})
    bm.normal_update()
    uv_layer = bm.loops.layers.uv.active
    flag = bm.faces.layers.int.get("is_piece")
    trot = bm.faces.layers.int.get("trot")
    zax = Vector((0, 0, 1))
    if not bm.verts:
        return
    def planar(face):
        nrm = face.normal
        if abs(nrm.z) < 0.7:  # vertical: a lo largo de la pared y altura
            t = zax.cross(nrm)
            if t.length > 1e-6:
                t.normalize()
            return [(l.vert.co.dot(t), l.vert.co.z) for l in face.loops]
        return [(l.vert.co.x, l.vert.co.y) for l in face.loops]  # horizontal: vista desde arriba

    glass, gls = [], []
    mapped, mins = [], {}
    atlas = p.get("_atlas")
    for face in bm.faces:
        if flag and face[flag]:
            continue  # las piezas propias conservan sus UVs
        if face.material_index == MAT["glass"]:
            glass.append(face)
            continue
        if face.material_index == MAT["gls"]:
            gls.append(face)
            continue
        region = ATLAS_BY_MAT.get(face.material_index, "facade")
        if atlas and region not in atlas:
            region = "facade"
        pts = planar(face)
        nrm = face.normal
        turn = face_uv_turn(p, region, face[trot] if trot is not None else 0)
        if turn:
            pts = _uv_turn(pts, turn)
        if abs(nrm.z) < 0.7:  # cada pared (plano) tiene su propio origen: la textura sigue continua entre caras
            key = (region, round(nrm.x, 2), round(nrm.y, 2), round(face.verts[0].co.dot(nrm), 2))
        else:
            key = (region, "top")
        if turn:
            key += (round(turn, 3),)
        mapped.append((face, region, key, pts))
        mn = mins.setdefault(key, [1e9, 1e9, -1e9, -1e9])
        mn[0], mn[1] = min(mn[0], min(q[0] for q in pts)), min(mn[1], min(q[1] for q in pts))
        mn[2], mn[3] = max(mn[2], max(q[0] for q in pts)), max(mn[3], max(q[1] for q in pts))
    # el LOD0 reparte el atlas según el tamaño de la pared más grande de cada material y fija la escala;
    # LOD1 y LOD2 los heredan (comparten texturas)
    atlas = p.get("_atlas")
    if not atlas:
        need = {}
        for key, (a0, b0, a1, b1) in mins.items():
            w0, h0 = need.get(key[0], (0.0, 0.0))
            need[key[0]] = (max(w0, a1 - a0), max(h0, b1 - b0))
        need.setdefault("facade", (1.0, 1.0))
        atlas, scale = layout_atlas(need)
        p["_atlas"], p["_ext"] = atlas, scale
    sc = p["_ext"]
    for face, region, key, pts in mapped:
        if region not in atlas:
            region = "facade"
        u0, v0, u1, v1 = atlas[region]
        mn0, mn1 = mins[key][0], mins[key][1]
        for loop, q in zip(face.loops, pts):
            loop[uv_layer].uv = (u0 + (q[0] - mn0) * sc, v0 + (q[1] - mn1) * sc)

    # ventanas: cada una en una celda de la textura de referencia; dentro de la celda, cada una en su
    # propio casillero (las UV de ventanas no pueden superponerse)
    seen = set()
    mode = p.get("win_rooms", "rooms")
    groups = []
    _gc = {f.index: f.calc_center_median() for f in glass}
    for f0 in sorted(glass, key=lambda f: (round(_gc[f.index].z, 2), round(_gc[f.index].x, 2), round(_gc[f.index].y, 2))):
        if f0.index in seen:
            continue
        group, stack = [], [f0]
        seen.add(f0.index)
        while stack:  # un grupo = caras de vidrio conectadas (una ventana)
            f = stack.pop()
            group.append(f)
            for e in f.edges:
                for g in e.link_faces:
                    if g.material_index == MAT["glass"] and g.index not in seen and not (flag and g[flag]):
                        seen.add(g.index)
                        stack.append(g)
        t = zax.cross(f0.normal)
        if abs(f0.normal.z) >= 0.7 or t.length < 1e-6:
            t = Vector((1, 0, 0))
        t.normalize()
        coords = [[(l.vert.co.dot(t), l.vert.co.z) for l in f.loops] for f in group]
        allc = [q for c in coords for q in c]
        center = f0.calc_center_median()
        groups.append({
            "faces": group, "coords": coords,
            "c0": (min(q[0] for q in allc) + max(q[0] for q in allc)) / 2,
            "c1": (min(q[1] for q in allc) + max(q[1] for q in allc)) / 2,
            "size": max(max(q[0] for q in allc) - min(q[0] for q in allc),
                        max(q[1] for q in allc) - min(q[1] for q in allc), 0.01),
            "idx": window_cell(mode, f"{round(center.x, 1)}|{round(center.y, 1)}|{round(center.z, 1)}|{p.get('seed', 0)}"),
        })
    by_cell = {}
    for g in groups:
        by_cell.setdefault(g["idx"], []).append(g)
    cell = 1.0 / WIN_GRID
    for idx, gs in by_cell.items():
        k = math.ceil(math.sqrt(len(gs)))
        slot = cell / k
        for j, g in enumerate(gs):
            cx = (idx % WIN_GRID) * cell + (j % k + 0.5) * slot
            cy = (idx // WIN_GRID) * cell + (j // k + 0.5) * slot
            sc = 0.8 * slot / g["size"]   # sin deformar la proporción
            for f, cs in zip(g["faces"], g["coords"]):
                for loop, q in zip(f.loops, cs):
                    loop[uv_layer].uv = (cx + (q[0] - g["c0"]) * sc, cy + (q[1] - g["c1"]) * sc)

    pack_islands_uv(gls, uv_layer)   # vidrio suelto: islas sin superponer


def pack_islands_uv(faces, uv_layer):
    """UV de caras sueltas (una isla por cara), empaquetadas en filas dentro de 0-1 sin superponerse."""
    zax = Vector((0, 0, 1))
    islands = []
    for f in faces:
        nrm = f.normal
        if abs(nrm.z) < 0.7:
            t = zax.cross(nrm)
            t.normalize()
            pts = [(l.vert.co.dot(t), l.vert.co.z) for l in f.loops]
        else:
            pts = [(l.vert.co.x, l.vert.co.y) for l in f.loops]
        mn0, mn1 = min(q[0] for q in pts), min(q[1] for q in pts)
        pts = [(q[0] - mn0, q[1] - mn1) for q in pts]
        w = max(q[0] for q in pts) or 0.01
        h = max(q[1] for q in pts) or 0.01
        islands.append((f, pts, w, h))
    if not islands:
        return
    order = sorted(range(len(islands)), key=lambda i: -islands[i][3])
    gap = 0.004

    def attempt(s):
        x = y = row = 0.0
        pos = {}
        for i in order:
            _f, _p, w, h = islands[i]
            ww, hh = w * s + gap, h * s + gap
            if x + ww > 1.0:
                x, y, row = 0.0, y + row, 0.0
            pos[i] = (x, y)
            x += ww
            row = max(row, hh)
        return y + row <= 1.0, pos
    lo, hi = 1e-5, 1.0
    for _ in range(30):
        mid = (lo + hi) / 2
        if attempt(mid)[0]:
            lo = mid
        else:
            hi = mid
    ok, pos = attempt(lo)
    for i, (f, pts, w, h) in enumerate(islands):
        ox, oy = pos[i]
        for loop, q in zip(f.loops, pts):
            loop[uv_layer].uv = (ox + q[0] * lo + gap / 2, oy + q[1] * lo + gap / 2)


def _poll_mesh(self, obj):
    return obj.type == "MESH"


# ----------------------------------------------------------------------------
# Techos procedurales (caras planas que miran hacia arriba de la forma)
# ----------------------------------------------------------------------------
ROOF_TYPES = [("FLAT", "Plano con parapet", "Terraza con parapet y cornisa"),
              ("SHED", "Una agua", "Un solo plano inclinado"),
              ("GABLE", "Dos aguas", "Caballete al medio y hastiales en los extremos"),
              ("HIP", "Cuatro aguas", "Cuatro planos; pirámide si la planta es cuadrada"),
              ("MANSARD", "Mansarda", "Cuatro aguas con quiebre: faldón empinado y tapa suave"),
              ("BUTTERFLY", "Mariposa", "Dos planos que bajan hacia un valle central (moderno)")]
ROOF_TYPE_ID = {k: i + 1 for i, (k, _n, _d) in enumerate(ROOF_TYPES)}
ROOF_ID_TYPE = {v: k for k, v in ROOF_TYPE_ID.items()}
ROOF_KEYS_INT = ("set", "type", "ridge", "flip", "ribs", "cap", "chn", "chcap", "dm", "dm_n", "dm_side", "capm", "nopar", "nocor")
ROOF_KEYS_FLOAT = ("pitch", "oe", "ov", "t", "pitch2", "brk", "rib_gap", "rib_h", "par_h", "par_t", "cor_h", "cor_o",
                   "chpos", "chside", "chw", "chd", "chh", "peak", "dm_w", "dm_h", "dm_q", "sd_fr", "sd_bk", "sd_lf", "sd_rg")
ROOF_DEFAULT = {
    "type": "FLAT", "pitch": 28.0, "oe": 0.45, "ov": 0.30, "t": 0.12, "ridge": 0, "flip": 0,
    "pitch2": 18.0, "brk": 1.3, "ribs": 1, "rib_gap": 0.22, "rib_h": 0.035, "cap": 1,
    "chn": 0, "chpos": 0.3, "chside": 0.0, "chw": 0.7, "chd": 0.7, "chh": 1.0, "chcap": 1, "peak": 0.0,
    "dm": 0, "dm_n": 2, "dm_side": 0, "dm_w": 1.6, "dm_h": 1.7, "dm_q": 0.25, "capm": 0,
    "nopar": 0, "nocor": 0, "par_h": 0.8, "par_t": 0.25, "cor_h": 0.3, "cor_o": 0.15,   # parapet y cornisa del techo plano
    "sd_fr": -1.0, "sd_bk": -1.0, "sd_lf": -1.0, "sd_rg": -1.0,   # alero por lado del edificio (-1 = el general)
}
ROOF_PRESETS = {
    "FLAT": ("Terraza plana", dict(type="FLAT")),
    "GABLE_TILE": ("Dos aguas con teja", dict(type="GABLE", pitch=28, oe=0.5, ov=0.35, t=0.12, ribs=1, cap=1, chn=0)),
    "GABLE_CHIM": ("Dos aguas con chimenea", dict(type="GABLE", pitch=30, oe=0.5, ov=0.35, t=0.12, ribs=1, cap=1, chn=1, chpos=0.3, chside=0.0, chcap=2)),
    "GABLE_SOFT": ("Dos aguas suave", dict(type="GABLE", pitch=17, oe=0.6, ov=0.4, t=0.12, ribs=1, cap=1, chn=0)),
    "GABLE_ALPINE": ("Chalet alpino (alero grande)", dict(type="GABLE", pitch=23, oe=1.1, ov=0.9, t=0.18, ribs=0, cap=0, chn=0)),
    "GABLE_METAL": ("Chapa moderna dos aguas", dict(type="GABLE", pitch=14, oe=0.3, ov=0.15, t=0.1, ribs=0, cap=0, chn=0)),
    "HIP_TILE": ("Cuatro aguas con teja", dict(type="HIP", pitch=26, oe=0.55, t=0.12, ribs=1, cap=1, chn=0)),
    "HIP_CHIM": ("Cuatro aguas con chimenea", dict(type="HIP", pitch=28, oe=0.55, t=0.12, ribs=1, cap=1, chn=1, chpos=0.35, chside=0.3, chcap=1)),
    "PYRAMID": ("Pabellón empinado", dict(type="HIP", pitch=38, oe=0.4, t=0.1, ribs=1, cap=1, chn=0)),
    "SHED_MODERN": ("Una agua moderno", dict(type="SHED", pitch=11, oe=0.3, ov=0.2, t=0.18, ribs=0, cap=0, chn=0)),
    "PEAKED_MODERN": ("Dos aguas asimétrico (moderno)", dict(type="GABLE", pitch=17, peak=0.45, oe=0.55, ov=0.5, t=0.2, ribs=0, cap=0, chn=0)),
    "BUTTERFLY": ("Mariposa", dict(type="BUTTERFLY", pitch=10, oe=0.5, ov=0.4, t=0.2, ribs=0, cap=0, chn=0)),
    "FLAT_SLAB": ("Losa plana con alero", dict(type="SHED", pitch=3, oe=0.6, ov=0.6, t=0.25, ribs=0, cap=0, chn=0)),
    "GABLE_DORMER": ("Dos aguas con buhardillas", dict(type="GABLE", pitch=38, oe=0.5, ov=0.35, t=0.12, ribs=1, cap=1, chn=0, dm=2, dm_n=2, dm_w=1.6, dm_h=1.7, dm_q=0.25)),
    "HIP_DORMER": ("Cuatro aguas con buhardillas", dict(type="HIP", pitch=40, oe=0.55, t=0.12, ribs=1, cap=1, chn=0, dm=3, dm_n=1, dm_w=1.6, dm_h=1.7, dm_q=0.25)),
    "MANSARD": ("Mansarda", dict(type="MANSARD", pitch=68, pitch2=16, brk=1.4, oe=0.25, t=0.1, ribs=0, cap=1, chn=1, chpos=0.5, chside=0.0, chcap=1)),
}



# Grupos de ajustes por pared hechos con una tabla (retícula estructural y planta baja comercial): cada uno tiene su bandera, sus
# propiedades del edificio (<clave>_<k>_g), las de la pared (<clave>_<k>) y sus atributos de cara (<pre><k>).
FLAG_GRID, FLAG_SHOP = 256, 512
_SURF_ITEMS = [("1", "Molduras", ""), ("2", "Fachada (A)", ""), ("3", "Material B", ""), ("4", "Material C", ""), ("5", "Material D", "")]
_WALLS_ITEMS = [("1", "Solo el frente", ""), ("2", "Frente y costados", ""), ("3", "Todas las paredes", "")]
NEW_GROUPS = {
    "gr": dict(flag=FLAG_GRID, use="use_gr", label="Retícula estructural", icon="MOD_LATTICE", pre="cs2_gr_",
               ints={"on": ("Retícula", [("0", "Ninguna", ""), ("1", "Pilastras y losas", ""), ("2", "Solo pilastras", ""),
                                         ("3", "Solo losas", "")], 0),
                     "walls": ("Dónde", _WALLS_ITEMS, 3),
                     "mat": ("Material del marco", _SURF_ITEMS, 1),
                     "every": ("Una losa cada", [("1", "Cada piso", ""), ("2", "Cada 2 pisos", ""), ("3", "Cada 3 pisos", "")], 1)},
               floats={"pw": ("Ancho de pilastra (m)", 0.1, 1.5, 0.35), "pd": ("Relieve de pilastra (m)", 0.03, 0.6, 0.2),
                       "sh": ("Alto de la losa (m)", 0.1, 1.2, 0.45), "sd": ("Relieve de la losa (m)", 0.03, 0.6, 0.15),
                       "bay": ("Separación sin ventanas (m)", 1.5, 8.0, 3.5)}),
    "sh": dict(flag=FLAG_SHOP, use="use_sh", label="Planta baja comercial", icon="SHADING_RENDERED", pre="cs2_sh_",
               ints={"on": ("Planta baja comercial", [("0", "No", ""), ("1", "Vidriera con pilares", "")], 0),
                     "walls": ("Dónde", _WALLS_ITEMS, 1),
                     "pmat": ("Material de pilares", _SURF_ITEMS, 1), "smat": ("Material del cartel", _SURF_ITEMS, 1),
                     "aw": ("Toldos", [("0", "Sin toldos", ""), ("1", "Uno por tramo", ""), ("2", "Corrido", "")], 0),
                     "awmat": ("Color del toldo", [("1", "Oscuro", ""), ("2", "Metal", ""), ("3", "Madera", "")], 1)},
               floats={"bay": ("Ancho de tramo (m)", 1.5, 8.0, 3.2), "pw": ("Ancho de pilar (m)", 0.15, 1.2, 0.4),
                       "pd": ("Relieve de pilar (m)", 0.02, 0.5, 0.12), "sign": ("Alto del cartel (m)", 0.2, 1.5, 0.6),
                       "signd": ("Relieve del cartel (m)", 0.02, 0.5, 0.12), "rec": ("Hundido del vidrio (m)", 0.0, 0.6, 0.15),
                       "ent": ("Hundido del acceso (m)", 0.0, 1.8, 0.0), "base": ("Zócalo bajo el vidrio (m)", 0.0, 1.2, 0.3),
                       "awd": ("Salida del toldo (m)", 0.4, 3.0, 1.2)}),
}
NEW_GROUP_INTS = tuple(g["pre"] + k for g in NEW_GROUPS.values() for k in g["ints"])
NEW_GROUP_FLOATS = tuple(g["pre"] + k for g in NEW_GROUPS.values() for k in g["floats"])


def group_cfg(p, cfg, key):
    """Valores de un grupo para una pared (dict): los de la pared si los personalizó, si no los generales del edificio (o None)."""
    g = NEW_GROUPS[key]
    c = cfg or {}
    if int(c.get("flags", 0)) & g["flag"]:
        pre = key + "_"
        out = {k: int(c.get(pre + k, d)) for k, (_l, _i, d) in g["ints"].items()}
        out.update({k: float(c.get(pre + k, d)) for k, (_l, _lo, _hi, d) in g["floats"].items()})
        out["_wall"] = True
        return out
    return (p.get("groups") or {}).get(key)


def group_on(p, cfg, key, n, zmin=0.0):
    """El grupo aplicado a una pared (dict de valores) o None si esa pared no lo lleva."""
    g = group_cfg(p, cfg, key)
    if not g or not int(g.get("on", 0)):
        return None
    if key == "sh" and zmin > 0.05:
        return None
    if g.get("_wall"):
        return g
    front, side = n.y < -0.7, abs(n.x) > 0.7
    mode = int(g.get("walls", 1))
    return g if (front or (mode >= 2 and side) or mode >= 3) else None


PORCH_INTS = ("on", "roof", "rail", "enc", "stp", "lv", "dmat", "pmat")
PORCH_FLOATS = ("d", "w", "off", "ext", "ph", "dh", "pitch", "sp", "sz")
PORCH_DEF = dict(on=2, roof=2, rail=2, enc=1, stp=1, lv=1, dmat=5, pmat=1,
                 d=1.5, w=3.2, off=0.0, ext=0.0, ph=2.5, dh=0.2, pitch=20.0, sp=2.2, sz=1.0)
WALL_INT_ATTRS = ("cs2_flags", "cs2_w_count", "cs2_door_mode", "cs2_bal_mode", "cs2_w_style", "cs2_d_style", "cs2_b_style",
                  "cs2_f_mat", "cs2_f_rot", "cs2_p_mode", "cs2_p_mat", "cs2_c_mat", "cs2_s_mode", "cs2_s_mat",
                  "cs2_k_mode", "cs2_k_bmat", "cs2_k_mmat", "cs2_k_fmat", "cs2_k_finn",
                  "cs2_k_prof", "cs2_k_pat", "cs2_k_patn", "cs2_k_basemat", "cs2_k_capmat") + tuple("cs2_o_" + k for k in PORCH_INTS) + tuple("cs2_r_" + k for k in ROOF_KEYS_INT) + NEW_GROUP_INTS
WALL_FLOAT_ATTRS = ("cs2_w_w", "cs2_w_h", "cs2_w_sill", "cs2_w_depth", "cs2_w_off",
                    "cs2_d_pos", "cs2_d_w", "cs2_d_h", "cs2_d_depth", "cs2_d_off",
                    "cs2_b_depth", "cs2_b_w", "cs2_b_rail", "cs2_b_off",
                    "cs2_p_pad", "cs2_p_prob", "cs2_c_w", "cs2_c_d", "cs2_s_gap", "cs2_s_w", "cs2_s_d",
                    "cs2_k_mw", "cs2_k_mh", "cs2_k_pt", "cs2_k_pd", "cs2_k_band", "cs2_k_edge", "cs2_k_find", "cs2_k_fint",
                    "cs2_k_ht", "cs2_k_hd", "cs2_k_patk", "cs2_k_doorw", "cs2_k_baseh", "cs2_k_caph", "cs2_k_capo", "cs2_k_cornw") + tuple("cs2_o_" + k for k in PORCH_FLOATS) + tuple("cs2_r_" + k for k in ROOF_KEYS_FLOAT) + NEW_GROUP_FLOATS
_CW_NEW_F = ("ht", "hd", "patk", "doorw", "baseh", "caph", "capo", "cornw")
_CW_NEW_I = ("prof", "pat", "patn", "basemat", "capmat")
FLAG_WIN, FLAG_DOOR, FLAG_BAL = 1, 2, 4
FLAG_PAN, FLAG_CAS, FLAG_SLT, FLAG_POR = 8, 16, 32, 64
FLAG_CW = 128


def wall_layers(bm, create=False):
    """Capas de ajustes por pared (atributos de cara de la malla forma)."""
    lays = {}
    for name in WALL_INT_ATTRS:
        lay = bm.faces.layers.int.get(name)
        if lay is None and create:
            lay = bm.faces.layers.int.new(name)
        if lay is not None:
            lays[name] = lay
    for name in WALL_FLOAT_ATTRS:
        lay = bm.faces.layers.float.get(name)
        if lay is None and create:
            lay = bm.faces.layers.float.new(name)
        if lay is not None:
            lays[name] = lay
    return lays


def face_cfg(f, lays):
    """Ajustes de una pared como dict corto ('w_w', 'flags', ...); None si no tiene ninguno."""
    cfg = {name[4:]: f[lay] for name, lay in lays.items()}
    if not cfg:
        return None
    if not (cfg.get("flags") or cfg.get("f_mat") or cfg.get("f_rot") or cfg.get("door_mode") or cfg.get("bal_mode")
            or cfg.get("w_style") or cfg.get("d_style") or cfg.get("b_style") or cfg.get("k_mode")):
        return None
    return cfg


def curved_chains(walls, curve_deg):
    """Cadenas de paredes vecinas que giran poco entre sí (una curva hecha con facetas): [[índices de `walls` en orden de recorrido]].

    Dos paredes se unen si comparten un borde vertical, son rectángulos de la misma altura, llevan los mismos ajustes y el giro entre
    sus normales es mayor que 0,3° y no pasa de `curve_deg`. Una cadena no cambia de sentido de giro (una S se parte en dos curvas)."""
    n_w = len(walls)
    if curve_deg <= 0 or n_w < 2:
        return []
    zax = Vector((0, 0, 1))
    info = []
    for pts, n, cfg in walls:
        u = zax.cross(n)
        a = [q.dot(u) for q in pts]
        z = [q.z for q in pts]
        zmin, zmax = min(z), max(z)
        rect = len(pts) == 4 and all(min(abs(zz - zmin), abs(zz - zmax)) < 1e-3 for zz in z)
        i0, i1 = min(range(len(pts)), key=lambda i: a[i]), max(range(len(pts)), key=lambda i: a[i])
        info.append(dict(rect=rect, zmin=zmin, zmax=zmax, S=Vector((pts[i0].x, pts[i0].y)), E=Vector((pts[i1].x, pts[i1].y)),
                         n=n, cfg=cfg or {}))
    link, sign = {}, {}
    cos_max = math.cos(math.radians(curve_deg))
    cos_min = math.cos(math.radians(0.3))
    for i, A in enumerate(info):
        if not A["rect"]:
            continue
        best = None
        for j, B in enumerate(info):
            if j == i or not B["rect"] or (A["E"] - B["S"]).length > 2e-3:
                continue
            if abs(A["zmin"] - B["zmin"]) > 1e-3 or abs(A["zmax"] - B["zmax"]) > 1e-3 or A["cfg"] != B["cfg"]:
                continue
            c = max(-1.0, min(1.0, A["n"].dot(B["n"])))
            if not (cos_max - 1e-9 <= c <= cos_min):
                continue
            if best is None or c > best[0]:
                best = (c, j)
        if best is not None:
            link[i] = best[1]
            sign[i] = 1 if A["n"].cross(info[best[1]]["n"]).z > 0 else -1
    targets = set(link.values())
    seen, chains = set(), []

    def walk(h):
        ch, cur, sg = [h], h, None
        seen.add(h)
        while cur in link and link[cur] not in seen:
            if sg is None:
                sg = sign[cur]
            elif sign[cur] != sg:
                break
            cur = link[cur]
            ch.append(cur)
            seen.add(cur)
        return ch
    for h in range(n_w):
        if h not in seen and h in link and h not in targets:
            chains.append(walk(h))
    for h in range(n_w):          # lo que queda: curvas cerradas (un círculo) o tramos que empiezan tras un cambio de giro
        if h not in seen and (h in link or h in targets):
            chains.append(walk(h))
    return [c for c in chains if len(c) >= 2]


def group_curved_walls(walls, curve_deg):
    """Marca cada pared de una curva con `_chain` (cadena, posición y largo total) para repartir sus aberturas a lo largo del arco."""
    chains = curved_chains(walls, curve_deg)
    if not chains:
        return walls
    walls = list(walls)
    zax = Vector((0, 0, 1))
    for k, ch in enumerate(chains):
        lens = []
        for i in ch:
            pts, n, _cfg = walls[i]
            u = zax.cross(n)
            a = [q.dot(u) for q in pts]
            lens.append(max(a) - min(a))
        total = sum(lens)
        nm = Vector((0, 0, 0))
        for i in ch:
            nm += walls[i][1]
        nm = nm.normalized() if nm.length > 1e-6 else walls[ch[0]][1]
        zs = [q.z for q in walls[ch[0]][0]]
        facets = []
        for i in ch:        # extremos (x, y) de cada faceta en el sentido de z x n: el balcón corrido los une en una sola franja
            pts, n, _cfg = walls[i]
            u = zax.cross(n)
            a = [q.dot(u) for q in pts]
            i0, i1 = min(range(len(pts)), key=lambda t: a[t]), max(range(len(pts)), key=lambda t: a[t])
            facets.append(dict(S=(pts[i0].x, pts[i0].y), E=(pts[i1].x, pts[i1].y), n=n))
        s0 = 0.0
        for pos, i in enumerate(ch):
            pts, n, cfg = walls[i]
            c2 = dict(cfg or {})
            c2["_chain"] = dict(id=k, idx=pos, count=len(ch), s0=s0, l=lens[pos], L=total, zmin=min(zs), zmax=max(zs), n=nm, wid=900 + k,
                                facets=facets)
            walls[i] = (pts, n, c2)
            s0 += lens[pos]
    return walls


def _slot_key_of(pts, n, center):
    """Clave geométrica de una pared (coordenadas del mundo): [x, y del inicio, x, y del final, z abajo, z arriba]."""
    u = Vector((0, 0, 1)).cross(n)
    a = [q.dot(u) for q in pts]
    i0, i1 = min(range(len(pts)), key=lambda i: a[i]), max(range(len(pts)), key=lambda i: a[i])
    S, E = pts[i0] + center, pts[i1] + center
    return [round(S.x, 3), round(S.y, 3), round(E.x, 3), round(E.y, 3),
            round(min(q.z for q in pts) + center.z, 3), round(max(q.z for q in pts) + center.z, 3)]


def slot_edits_load(obj):
    """Ediciones de ranuras guardadas en el objeto forma: {"walls": [{"key": [...], "ov": {...}, "add": [...]}]}."""
    raw = obj.get("cs2_slots") if obj is not None else None
    if not raw:
        return {"walls": []}
    try:
        d = json.loads(raw)
        return d if isinstance(d, dict) and isinstance(d.get("walls"), list) else {"walls": []}
    except (TypeError, ValueError):
        _log_exc("ranuras_guardadas")
        return {"walls": []}


def slot_edits_match(data, key, tol=0.3):
    """La entrada de `data` para una pared de clave `key` (la más cercana dentro de la tolerancia) o None."""
    best = None
    for w in data.get("walls", []):
        k2 = w.get("key")
        if not k2 or len(k2) != 6:
            continue
        d = max(abs(a - b) for a, b in zip(key, k2))
        if d <= tol and (best is None or d < best[0]):
            best = (d, w)
    return best[1] if best else None


def attach_slot_edits(walls, center, obj):
    """Pone en cada pared (o curva) las ediciones de ranuras que le corresponden (cfg["_slots"])."""
    data = slot_edits_load(obj)
    if not data["walls"]:
        return walls
    walls = list(walls)
    chain_key = {}
    for i, (pts, n, cfg) in enumerate(walls):
        ch = (cfg or {}).get("_chain")
        if ch:
            if ch["id"] not in chain_key:
                fs = ch["facets"]
                chain_key[ch["id"]] = [round(fs[0]["S"][0] + center.x, 3), round(fs[0]["S"][1] + center.y, 3),
                                       round(fs[-1]["E"][0] + center.x, 3), round(fs[-1]["E"][1] + center.y, 3),
                                       round(ch["zmin"] + center.z, 3), round(ch["zmax"] + center.z, 3)]
            key = chain_key[ch["id"]]
        else:
            key = _slot_key_of(pts, n, center)
        ent = slot_edits_match(data, key)
        if ent:
            c2 = dict(cfg or {})
            c2["_slots"] = ent
            walls[i] = (pts, n, c2)
    return walls


def read_shape(obj, recalc=True, fh=None, curve_deg=0.0):
    """Lee la malla base en coordenadas del mundo, centrada en XY y apoyada en z=0.

    Paredes: caras verticales. Techos: caras que miran hacia arriba.
    Las caras coplanares con los mismos ajustes se fusionan para que las paredes grandes
    tengan un solo ritmo de ventanas. Con `fh`, las alturas se ajustan a múltiplos de piso.
    """
    try:
        if obj.mode == "EDIT":
            obj.update_from_editmode()
    except Exception:
        _log_exc("read_shape")
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    bm.transform(obj.matrix_world)
    if recalc:
        bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    if fh and len(bm.verts):
        zbase = min(v.co.z for v in bm.verts)
        for v in bm.verts:
            v.co.z = round((v.co.z - zbase) / fh) * fh + zbase
        bmesh.ops.remove_doubles(bm, verts=list(bm.verts), dist=1e-4)
        flat = [f for f in bm.faces if f.calc_area() < 1e-6]
        if flat:
            bmesh.ops.delete(bm, geom=flat, context="FACES_ONLY")
    lays = wall_layers(bm)
    if lays:
        keyf = lambda f: tuple(f[l] for l in lays.values())
        same = [e for e in bm.edges if len(e.link_faces) == 2 and keyf(e.link_faces[0]) == keyf(e.link_faces[1])]
    else:
        same = list(bm.edges)
    # se funden las caras iguales y coplanares sin tocar los vértices: dissolve_limit con vértices
    # dejaba triángulos cuando se subdividía una pared (la cara perdía una esquina y no recibía aberturas)
    bm.normal_update()
    cosl = math.cos(math.radians(0.5))
    flat = [e for e in same if e.is_valid and len(e.link_faces) == 2
            and e.link_faces[0].normal.dot(e.link_faces[1].normal) > cosl]
    bmesh.ops.dissolve_edges(bm, edges=flat, use_verts=False, use_face_split=False)
    bm.normal_update()
    if len(bm.faces) == 0:
        bm.free()
        raise RuntimeError("La forma base no tiene caras")

    xs = [v.co.x for v in bm.verts]
    ys = [v.co.y for v in bm.verts]
    zs = [v.co.z for v in bm.verts]
    center = Vector(((min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2, min(zs)))

    walls, roofs, parapet_edges = [], [], []
    for f in bm.faces:
        n = f.normal.copy()
        pts = [v.co - center for v in f.verts]
        if abs(n.z) < 0.12:
            nh = Vector((n.x, n.y, 0.0))
            if nh.length > 1e-6:
                walls.append((pts, nh.normalized(), face_cfg(f, lays)))
        elif n.z > 0.12:
            roofs.append((pts, n, roof_face_cfg(f, lays) if lays else None))
    for e in bm.edges:  # borde entre un techo plano y una pared: lleva parapet y cornisa
        if len(e.link_faces) < 2 or abs(e.verts[0].co.z - e.verts[1].co.z) > 0.01:
            continue
        ez = e.verts[0].co.z
        if any(abs(f.normal.z) < 0.12 and min(v.co.z for v in f.verts) >= ez - 0.01 for f in e.link_faces):
            continue  # hay una pared que ARRANCA en este borde y sube: no lleva parapet ni cornisa
        roof = wall = None
        for f in e.link_faces:
            if f.normal.z > 0.9:
                roof = f
            elif abs(f.normal.z) < 0.12:
                wall = f
        if roof is not None and wall is not None:
            nw = Vector((wall.normal.x, wall.normal.y, 0.0))
            if nw.length > 1e-6:
                parapet_edges.append((e.verts[0].co - center, e.verts[1].co - center, nw.normalized(),
                                      roof_face_cfg(roof, lays) if lays else None))
    size = Vector((max(xs) - min(xs), max(ys) - min(ys), max(zs) - min(zs)))
    bm.free()
    walls = attach_slot_edits(group_curved_walls(walls, curve_deg), center, obj)
    return {"walls": walls, "roofs": roofs, "parapet_edges": parapet_edges, "center": center, "size": size}


def poly_intervals(poly, z):
    """Tramos [a0, a1] que el polígono 2D (a, z) ocupa a la altura z."""
    cross = []
    n = len(poly)
    for i in range(n):
        (a0, z0), (a1, z1) = poly[i], poly[(i + 1) % n]
        if (z0 <= z < z1) or (z1 <= z < z0):
            cross.append(a0 + (z - z0) / (z1 - z0) * (a1 - a0))
    cross.sort()
    return list(zip(cross[0::2], cross[1::2]))


def rect_fits(poly, a0, a1, z0, z1, margin=0.15):
    for zz in (z0 + 0.02, (z0 + z1) / 2, z1 - 0.02):
        if not any(s - 1e-4 <= a0 - margin and a1 + margin <= e + 1e-4 for s, e in poly_intervals(poly, zz)):
            return False
    return True


def _cols_around_door(amin, amax, d0, d1, nn, ww):
    """Bordes izquierdos de `nn` ventanas repartidas a los lados de la puerta (se ajustan solas al espacio libre)."""
    segs = [(amin, d0 - 0.2), (d1 + 0.2, amax)]
    segs = [(a, b) for a, b in segs if b - a >= ww + 0.3]
    if not segs:
        return []
    cap = [max(1, int((b - a - 0.1) // (ww + 0.3))) for a, b in segs]
    take = [0] * len(segs)
    for _ in range(nn):
        best = max((i for i in range(len(segs)) if take[i] < cap[i]),
                   key=lambda i: (segs[i][1] - segs[i][0]) / (take[i] + 1), default=None)
        if best is None:
            break
        take[best] += 1
    cols = []
    for (a, b), k in zip(segs, take):
        if k:
            gap = ((b - a) - k * ww) / (k + 1)
            cols += [a + gap * (j + 1) + ww * j for j in range(k)]
    return sorted(cols)


def _hq(*key):
    """Número fijo entre 0 y 1 para una clave (decide qué detalles entran en un LOD según su presupuesto)."""
    return (zlib.crc32(repr(key).encode()) % 10007) / 10007.0


SLOT_KINDS = [("win", "Ventana", ""), ("door", "Puerta", ""), ("bal", "Balcón", ""), ("shop", "Vidriera", ""), ("none", "Vacío", "")]


def _slot_kind_of(op, bals):
    """Tipo de una abertura ya armada: door, shop (vidriera), bal (puerta vidriada con balcón) o win."""
    if op[4] in (MAT["door"], MAT["gar"]):
        return "door"
    if op[4] == MAT["gls"]:
        return "shop"
    c = (op[0] + op[1]) / 2
    return "bal" if any(abs(b[0] - c) < 0.01 and abs(b[1] - op[2]) < 0.02 for b in bals) else "win"


def _slot_defaults(kind, G):
    """(ancho, alto, antepecho) de un tipo de ranura cuando no hay medidas propias."""
    fh = G["fh"]
    if kind == "door":
        return G["dw"], G["dh"], 0.0
    if kind == "bal":
        return G["ww"], 2.1, 0.0
    if kind == "shop":
        return 2.0, max(1.0, fh - 0.95), 0.3
    return G["ww"], G["wh"], G["sill"]


def _slot_build(p, G, e, tag, op, kind0, bal0):
    """Una ranura con sus ajustes propios: (abertura, profundidad, balcón o None, estilo o None) o None si queda vacía o no entra.
    e = ajustes de la ranura (kind, w, h, sill, da, style, a, f)."""
    kind = e.get("kind") or kind0 or "win"
    if kind == "none" or e.get("del"):
        return None
    f = tag[0] if (tag and isinstance(tag[0], int)) else int(e.get("f", 1))
    zb = G["zmin"] + f * G["fh"]
    if op is not None:
        c, w0, h0, s0 = (op[0] + op[1]) / 2, op[1] - op[0], op[3] - op[2], op[2] - zb
        if kind != kind0:
            w0, h0, s0 = _slot_defaults(kind, G)
    else:
        c = float(e.get("a", (G["amin"] + G["amax"]) / 2))
        w0, h0, s0 = _slot_defaults(kind, G)
    c += float(e.get("da", 0.0))
    w, h = float(e.get("w", w0)), float(e.get("h", h0))
    sill = float(e.get("sill", s0)) if kind in ("win", "shop") else 0.0
    a0, a1 = max(c - w / 2, G["amin"] + 0.12), min(c + w / 2, G["amax"] - 0.12)
    z0 = zb + sill
    z1 = min(z0 + h, G["zmax"] - 0.15)
    if a1 - a0 < 0.4 or z1 - z0 < 0.3 or z0 < G["zmin"] - 0.01 or not rect_fits(G["poly"], a0, a1, z0, z1):
        return None
    mat = {"win": MAT["glass"], "bal": MAT["glass"], "door": MAT["door"], "shop": MAT["gls"]}[kind]
    dep = G["ddepth"] if kind == "door" else (0.15 if kind == "shop" else G["wdepth"])
    st, bal = None, None
    name = e.get("style")
    if name:
        st = False if name == "__NONE__" else get_style(name)
    if kind == "bal":
        bst = G["bstyle"] if not (G["bstyle"] and G["bstyle"].get("bal_type") == "LOGGIA") else None
        if name and name != "__NONE__" and st:
            bst = st
        bal = ((a0 + a1) / 2, zb, G["bd"], G["bw"], G["rail"], bst, G["ww"])
        st = None
    return (a0, a1, z0, z1, mat), dep, bal, st


def apply_slot_edits(p, E, ops, depths, tags, bals, G):
    """Aplica a las aberturas automáticas de una pared lo que el usuario editó a mano (borrar, cambiar tipo, medidas, estilo,
    correr) y suma las ranuras agregadas. Devuelve (aberturas, profundidades, balcones, estilos por abertura)."""
    ov, adds = E.get("ov") or {}, E.get("add") or []
    kept, claimed, ss = [], set(), {}
    rects = []
    for op, dep, tag in zip(ops, depths, tags):
        kind0 = _slot_kind_of(op, bals)
        c = (op[0] + op[1]) / 2
        bal0 = next((b for b in bals if abs(b[0] - c) < 0.01 and abs(b[1] - op[2]) < 0.02), None) if kind0 == "bal" else None
        if bal0 is not None:
            claimed.add(id(bal0))
        e = ov.get(f"{tag[0]},{tag[1]}") if tag else None
        res = (op, dep, bal0, None) if not e else _slot_build(p, G, e, tag, op, kind0, bal0)
        if res is None and e:
            continue
        kept.append((res, e is not None, (op, dep, bal0, None)))
    out, out_bals = [], []

    def conflict(r):
        return any(min(r[1], q[1]) - max(r[0], q[0]) > 0.05 and min(r[3], q[3]) - max(r[2], q[2]) > 0.05 for q, _d in out)
    for res, edited, orig in kept:
        if edited and conflict(res[0]):
            res = orig
        out.append((res[0], res[1]))
        if res[2] is not None:
            out_bals.append(res[2])
        if res[3] is not None:
            ss[(round(res[0][0], 2), round(res[0][2], 2))] = res[3]
    for i, e in enumerate(adds):
        res = _slot_build(p, G, e, ("add", i), None, None, None)
        if res is None or conflict(res[0]):
            continue
        out.append((res[0], res[1]))
        if res[2] is not None:
            out_bals.append(res[2])
        if res[3] is not None:
            ss[(round(res[0][0], 2), round(res[0][2], 2))] = res[3]
    out_bals += [b for b in bals if id(b) not in claimed]
    return [o_[0] for o_ in out], [o_[1] for o_ in out], out_bals, ss


def _chain_keep_bal(p, cfg, n):
    """¿Una faceta de una curva lleva balcones? Si mira al frente, si se pidieron en todas las paredes o si esta los pide siempre."""
    return n.y < -0.7 or p.get("balcony_walls") == "all" or int(cfg.get("bal_mode", 0)) in (2, 3)


def _chain_virtual(p, cfg, ch, lod):
    """Aberturas de toda una curva en la pared desenrollada: (aberturas, balcones, profundidades, estilos por abertura). Si `cfg` lleva
    «_slots» se aplican; la interfaz de ranuras la llama sin ellas para ver el reparto automático. Deja p["_slot_last"] del arco entero."""
    base = {k: v for k, v in cfg.items() if k != "_chain"}
    vp = [(0.0, ch["zmin"]), (ch["L"], ch["zmin"]), (ch["L"], ch["zmax"]), (0.0, ch["zmax"])]
    p2 = p
    gsh = (p.get("groups") or {}).get("sh")
    if gsh or base.get("flags", 0) & FLAG_SHOP:      # vidriera sobre una curva: un tramo por faceta (los pilares caen en las uniones)
        avg = ch["L"] / max(1, ch["count"])
        p2 = dict(p, groups=dict(p.get("groups") or {}))
        if gsh:
            p2["groups"]["sh"] = dict(gsh, bay=min(gsh["bay"], avg))
        if base.get("flags", 0) & FLAG_SHOP:
            base["sh_bay"] = min(float(base.get("sh_bay", 3.2)), avg)
    res_ = shape_openings(p2, vp, Vector((0.0, -1.0, 0.0)), ch["wid"], lod, base)
    if p2 is not p:
        p["_slot_last"] = p2.get("_slot_last")
    return (*res_, dict(p2.get("_ss") or {}))


def _chain_openings(p, poly, n, lod, cfg, ch):
    """Aberturas de una faceta de una curva: se reparten en la pared desenrollada (todo el arco) y a cada faceta le toca lo que
    cae en ella. Una abertura que cruza el borde se corre hacia adentro de la faceta; si no entra, se descarta.
    La pared desenrollada se arma como si mirara al frente; después, cada faceta se queda con puertas y balcones solo si ella
    misma mira al frente (o si el balcón/puerta se pidió siempre), y en las demás esos huecos pasan a ser ventanas."""
    cache = p.setdefault("_chain_cache", {})
    key = (ch["id"], lod)
    if key not in cache:
        cache[key] = _chain_virtual(p, cfg, ch, lod)
    ops, bals, depths, ss_all = cache[key]
    ss_f = {}
    base_cfg = {k: v for k, v in cfg.items() if k != "_chain"}
    front = n.y < -0.7
    keep_bal = _chain_keep_bal(p, cfg, n)
    keep_door = front or int(cfg.get("door_mode", 0)) == 1
    fh = p["floor_h"]
    sill = max(0.0, p["sill"])
    wh = max(0.4, min(p["win_h"], fh - sill - 0.3))
    s0, ln = ch["s0"], ch["l"]
    last = ch["idx"] == ch["count"] - 1
    amin_f = min(q[0] for q in poly)
    lo, hi = s0 + 0.12, s0 + ln - 0.12
    bal_at = {round((b[0]), 3): b for b in bals}
    o_ops, o_depths, shifts, as_window = [], [], [], set()
    for op, dep in zip(ops, depths):
        c = (op[0] + op[1]) / 2
        if not (s0 <= c < s0 + ln or (last and c <= s0 + ln + 1e-9)):
            continue
        w = op[1] - op[0]
        is_bal = round(c, 3) in bal_at
        if w > hi - lo + 1e-6:
            # faceta más corta que la abertura: una ventana se angosta para entrar (mínimo 0,6 m); puertas y balcones no
            if op[4] not in (MAT["glass"], MAT["gls"]) or is_bal or hi - lo < 0.6:
                continue
            w = hi - lo
            op = (c - w / 2, c + w / 2) + tuple(op[2:])
        if op[4] == MAT["door"] and not keep_door:
            continue
        na0 = min(max(op[0], lo), hi - w)
        loc = amin_f + (na0 - s0)
        new = (loc, loc + w) + tuple(op[2:])
        if op[4] == MAT["gls"] and op[2] < ch["zmin"] + fh and not group_on(p, base_cfg, "sh", n, ch["zmin"]):
            new = (new[0], new[1], op[2] + sill, op[2] + sill + wh, MAT["glass"])      # esta faceta no lleva vidriera: ventana común
            dep = p["win_depth"]
        if is_bal and not keep_bal:       # sin balcón en esta faceta: el hueco de puerta vidriada pasa a ser una ventana
            new = (new[0], new[1], op[2] + sill, op[2] + sill + wh, MAT["glass"])
            dep = p["win_depth"]
            as_window.add(round(c, 3))
        o_ops.append(new)
        o_depths.append(dep)
        shifts.append((c, loc + w / 2))
        if (round(op[0], 2), round(op[2], 2)) in ss_all and new[4] == op[4]:
            ss_f[(round(new[0], 2), round(new[2], 2))] = ss_all[(round(op[0], 2), round(op[2], 2))]
    o_bals = []
    for b in bals:
        if round(b[0], 3) in as_window or not keep_bal:
            continue
        if b[5] and b[5].get("bal_run"):       # corrido: la losa de todo el piso la arma una sola faceta (_emit_chain_runs)
            continue
        m = next((new for old, new in shifts if abs(old - b[0]) < 0.05), None)
        if m is None or ln - 0.3 < 0.8:
            continue
        o_bals.append((m, b[1], b[2], b[3] if b[3] is None else min(b[3], ln - 0.3)) + tuple(b[4:]))   # ancho None: lo decide el estilo
    p["_ss"] = ss_f
    return o_ops, o_bals, o_depths


def shape_openings(p, poly, n, wid, lod, cfg=None):
    """Aberturas de una pared con sus ajustes.

    Devuelve (aberturas [(a0, a1, z0, z1, mat)], balcones [(a_medio, z, prof, ancho, baranda)],
    profundidades [una por abertura]).
    """
    cfg = cfg or {}
    if cfg.get("_chain"):
        return _chain_openings(p, poly, n, lod, cfg, cfg["_chain"])
    fl = int(cfg.get("flags", 0))
    a_s = [q[0] for q in poly]
    z_s = [q[1] for q in poly]
    amin, amax, zmin, zmax = min(a_s), max(a_s), min(z_s), max(z_s)
    fh = p["floor_h"]
    ww, wh, sill, wdepth = p["win_w"], p["win_h"], p["sill"], p["win_depth"]
    if fl & FLAG_WIN:
        ww = cfg.get("w_w") or ww
        if cfg.get("w_sill") is not None:  # 0.0 es un antepecho válido (ventana a ras de piso)
            sill = max(0.0, cfg["w_sill"])
        wh = cfg.get("w_h") or wh
        wdepth = max(0.0, cfg.get("w_depth", wdepth))
    wh = max(0.4, min(wh, fh - sill - 0.3))
    length = amax - amin
    if length < ww + 1.0:
        return [], [], []
    var = p["variation"]
    no_win = bool(fl & FLAG_WIN) and int(cfg.get("w_count", 0)) < 0   # 0 ventanas pedidas en esta pared
    if (fl & FLAG_WIN) and int(cfg.get("w_count", 0)) > 0:
        nn = max(1, min(int(cfg["w_count"]), int(length // (ww + 0.3))))
        gap = (length - nn * ww) / (nn + 1)
        base_cols = [gap * (k + 1) + ww * k for k in range(nn)]
    else:
        base_cols = window_columns(length, ww, p["min_gap"])
    off = cfg.get("w_off", 0.0) if fl & FLAG_WIN else 0.0
    cols = [amin + c + off for c in base_cols]
    front = n.y < -0.7  # mira hacia -Y
    ground = zmin <= 0.05
    bmode = int(cfg.get("bal_mode", 0))
    bal_ok = front or p["balcony_walls"] == "all"
    bstyle = style_for_balcony(p, cfg)
    loggia = bool(bstyle) and bstyle.get("bal_type") == "LOGGIA"
    if bstyle:  # el estilo decide medidas, salvo que la pared las personalice
        bd = bw = rail = None
    else:
        bd, bw, rail = p["balcony_depth"], (p.get("bal_w_g") or ww + 0.8), p.get("bal_rail_g", 0.9)
    if fl & FLAG_BAL:
        bd = cfg.get("b_depth") or bd
        bw = cfg.get("b_w") or bw
        rail = cfg.get("b_rail") or rail

    floor0 = int(round(zmin / fh))  # piso absoluto del arranque de esta cara

    def balcony_at(f, k):
        if floor0 + f == 0:
            return False
        if bmode == 1:
            return False
        if bmode == 2:
            return True
        if bmode == 3:
            return k % 2 == 0
        return bal_ok and has_balcony(p, floor0 + f, k, wid + 1)

    ops, bals, depths = [], [], []
    tags = []        # (piso, columna) de cada abertura automática; la puerta es (0, -1)
    door = None
    dmode = int(cfg.get("door_mode", 0))
    if dmode == 1 or (dmode == 0 and front and ground):
        dw = p.get("door_w_g") or min(max(ww, 1.2), 1.5)
        dh = p.get("door_h_g") or min(2.2, fh - 0.4)
        ddepth = wdepth
        _dst = style_for(p, MAT["door"], cfg)
        if _dst and float(_dst.get("size_w", 0) or 0) > 0:
            dw = float(_dst["size_w"])
            dh = float(_dst.get("size_h", 0) or 0) or dh
        dw = min(dw, max(1.0, length - 0.8))
        dh = min(dh, fh - 0.2)
        if fl & FLAG_DOOR:
            dw = cfg.get("d_w") or dw
            dh = cfg.get("d_h") or dh
            ddepth = max(0.0, cfg.get("d_depth", ddepth))
            ctr = amin + min(max(cfg.get("d_pos", 0.5), 0.0), 1.0) * length + cfg.get("d_off", 0.0)
        elif cols:
            ctr = cols[len(cols) // 2] + ww / 2
        else:
            ctr = amin + length / 2
        ctr = min(max(ctr, amin + dw / 2 + 0.2), amax - dw / 2 - 0.2)
        rect = (ctr - dw / 2, ctr + dw / 2, zmin, zmin + dh, MAT["door"])
        if zmin + dh <= zmax - 0.15 and rect_fits(poly, rect[0], rect[1], rect[2], rect[3]):
            door = rect
            ops.append(rect)
            depths.append(ddepth)
            tags.append((0, -1))
    if door and (fl & FLAG_WIN) and int(cfg.get("w_count", 0)) > 0:
        cols = _cols_around_door(amin, amax, door[0], door[1], int(cfg["w_count"]), ww) 
        cols = [c + off for c in cols]
    nfl = max(1, int(math.ceil((zmax - zmin) / fh - 1e-3)))
    for f in range(nfl):
        base = zmin + f * fh
        for k, u0 in enumerate(cols):
            is_bal = False
            if no_win and not balcony_at(f, k):
                continue
            if balcony_at(f, k) and loggia:
                bsd = {**STYLE_DEFAULT, **bstyle}
                pitch = (cols[1] - cols[0]) if len(cols) > 1 else ww + 1.2
                near = any(0 <= j < len(cols) and balcony_at(f, j) for j in (k - 1, k + 1))
                cap = (pitch - 0.35) if near else 2 * (pitch - ww / 2 - 0.25)
                wl = max(ww, min(ww + bsd["bal_extra_w"], cap))
                cxl = u0 + ww / 2
                rect = (cxl - wl / 2, cxl + wl / 2, base, base + fh - 0.3, MAT["facade"] if lod == 0 else MAT["glass"])
                is_bal = True
                loggia_dep = bsd["bal_depth"]
            elif balcony_at(f, k):
                rect = (u0, u0 + ww, base, base + 2.1, MAT["glass"])
                is_bal = True
            else:
                h = wh
                if var > 0:
                    h = wh * (1 + (rng_for(p, "win", wid, floor0 + f, k).random() - 0.5) * 0.6 * var)
                    h = max(0.6, min(h, fh - sill - 0.45))
                rect = (u0, u0 + ww, base + sill, base + sill + h, MAT["glass"])
            if is_bal and (fl & FLAG_BAL) and cfg.get("b_off"):
                rect = (rect[0] + cfg["b_off"], rect[1] + cfg["b_off"]) + tuple(rect[2:])
            if rect[2] < zmin - 0.01 or rect[3] > zmax - 0.15:
                continue
            if door and not (rect[1] <= door[0] - 0.2 or rect[0] >= door[1] + 0.2
                             or rect[3] <= door[2] or rect[2] >= door[3]):
                continue  # la puerta ocupa ese lugar
            if not rect_fits(poly, rect[0], rect[1], rect[2], rect[3]):
                continue
            ops.append(rect)
            tags.append((f, k))
            depths.append(loggia_dep if (is_bal and loggia) else wdepth)
            if is_bal and loggia:
                bals.append(((rect[0] + rect[1]) / 2, base, loggia_dep, rect[1] - rect[0], rail, bstyle, ww))
            elif is_bal:
                bals.append(((rect[0] + rect[1]) / 2, base, bd, bw, rail, bstyle, ww))
    sh = group_on(p, cfg, "sh", n, zmin)
    if sh and ground and length >= 2.0:
        tag_of = {id(op_): tg for op_, tg in zip(ops, tags)}
        ops, depths = _shop_ops(p, sh, ops, depths, amin, amax, zmin, fh)
        tags = [tag_of.get(id(op_)) for op_ in ops]
    p["_slot_last"] = dict(ops=list(ops), tags=list(tags), bals=list(bals), zmin=zmin, zmax=zmax, amin=amin, amax=amax, fh=fh,
                           ww=ww, wh=wh, sill=sill, dw=min(max(ww, 1.2), 1.5), dh=min(2.2, fh - 0.4))
    p["_ss"] = {}
    E = cfg.get("_slots")
    if E:
        G = dict(poly=poly, amin=amin, amax=amax, zmin=zmin, zmax=zmax, fh=fh, ww=ww, wh=wh, sill=sill, wdepth=wdepth,
                 ddepth=locals().get("ddepth", wdepth), dw=min(max(ww, 1.2), 1.5), dh=min(2.2, fh - 0.4),
                 bd=bd, bw=bw, rail=rail, bstyle=bstyle)
        ops, depths, bals, p["_ss"] = apply_slot_edits(p, E, ops, depths, tags, bals, G)
    return ops, bals, depths


def add_obox(bm, c, ax, ay, sx, sy, sz, mat, skip=()):
    """Caja orientada: ax = eje X local, ay = eje Y local (hacia adentro de la pared), Z hacia arriba."""
    hx, hy, hz = sx / 2, sy / 2, sz / 2
    zax = Vector((0, 0, 1))
    v = [bm.verts.new(c + ax * (dx * hx) + ay * (dy * hy) + zax * (dz * hz))
         for dz in (-1, 1) for dy in (-1, 1) for dx in (-1, 1)]
    sides = {
        "-z": (0, 2, 3, 1), "+z": (4, 5, 7, 6),
        "-y": (0, 1, 5, 4), "+y": (2, 6, 7, 3),
        "-x": (0, 4, 6, 2), "+x": (1, 3, 7, 5),
    }
    for name, idx in sides.items():
        if name in skip:
            continue
        bm.faces.new([v[i] for i in idx]).material_index = mat


def _miter(n1, n2):
    """Vector que, multiplicado por la distancia, da el desplazamiento de la esquina entre dos paredes."""
    if n2 is None:
        return n1
    k = 1.0 + n1.dot(n2)
    if k < 0.15:  # casi plegado sobre sí mismo: sin ingletado
        return n1
    return (n1 + n2) / k


def add_band_group(bm, segs, d_in, d_out, mat):
    """Molduras horizontales con esquinas ingletadas.
    segs = [(A, B, n, z0, z1)]: A y B puntos del plano, n normal exterior. Las uniones entre segmentos que
    comparten extremo (misma altura) se cortan a inglete y no llevan tapa; los extremos libres sí."""
    fixed = []
    for sg in segs:
        A, B, n, z0, z1 = sg[:5]
        if (B - A).cross(Vector((0, 0, 1))).dot(n) < 0:  # que A->B sea z x n (normales hacia afuera)
            A, B = B, A
        fixed.append((A, B, n, z0, z1, sg[5] if len(sg) > 5 else d_out))
    segs = fixed
    ends = {}
    for i, (A, B, n, z0, z1, _do) in enumerate(segs):
        for e, P in ((0, A), (1, B)):
            ends.setdefault((round(P.x, 3), round(P.y, 3), round(z0, 3), round(z1, 3)), []).append((i, e))
    for i, (A, B, n, z0, z1, d_out) in enumerate(segs):
        ms, free = [], []
        for e, P in ((0, A), (1, B)):
            key = (round(P.x, 3), round(P.y, 3), round(z0, 3), round(z1, 3))
            others = [(j, ee) for j, ee in ends.get(key, []) if j != i]
            nb = segs[others[0][0]][2] if others else None
            ms.append(_miter(n, nb))
            free.append(nb is None)
        ring = []
        for P, m in zip((A, B), ms):
            ring.append([Vector((P.x, P.y, 0)) + m * d_out, Vector((P.x, P.y, 0)) - m * d_in])
        v = {}
        for e in (0, 1):
            for k, zz in ((0, z0), (1, z1)):
                for j in (0, 1):
                    c = ring[e][j]
                    v[(e, k, j)] = bm.verts.new(Vector((c.x, c.y, zz)))
        def F(*keys):
            try:
                bm.faces.new([v[k] for k in keys]).material_index = mat
            except ValueError:
                pass
        # j=0 exterior, j=1 interior; e extremo; k 0 abajo / 1 arriba
        F((0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0))   # frente
        F((0, 1, 1), (1, 1, 1), (1, 0, 1), (0, 0, 1))   # dorso
        F((0, 1, 0), (1, 1, 0), (1, 1, 1), (0, 1, 1))   # arriba
        F((0, 0, 1), (1, 0, 1), (1, 0, 0), (0, 0, 0))   # abajo
        if free[0]:
            F((0, 0, 0), (0, 1, 0), (0, 1, 1), (0, 0, 1))
        if free[1]:
            F((1, 0, 1), (1, 1, 1), (1, 1, 0), (1, 0, 0))


def add_edge_box(bm, p0, p1, nw, thick, out, z0, z1, mat):
    """Caja a lo largo de un borde de techo (parapet o cornisa). nw = normal exterior de la pared."""
    zax = Vector((0, 0, 1))
    d = zax.cross(nw).normalized()
    ay = -nw
    length = abs((p1 - p0).dot(d)) + 2 * out
    mid = (p0 + p1) / 2
    c = Vector((mid.x, mid.y, (z0 + z1) / 2)) + ay * ((thick - 2 * out) / 2)
    add_obox(bm, c, d, ay, length, thick, z1 - z0, mat)


def _bisect(tmp, co, no):
    geom = list(tmp.verts) + list(tmp.edges) + list(tmp.faces)
    bmesh.ops.bisect_plane(tmp, geom=geom, dist=1e-5, plane_co=co, plane_no=no)


SURF_MATS = {1: "trim", 2: "facade", 3: "facb", 4: "facc", 5: "facd"}
PAN_MATS = {1: "facb", 2: "facc", 3: "facd"}


def look_cfg(p, cfg):
    """Revestimiento efectivo de una pared: lo global, salvo lo que la pared personalice."""
    g = dict(pan_mode=1, pan_mat=1, pan_pad=0.35, pan_prob=0.5, cas_w=0.0, cas_d=0.08, cas_mat=1,
             slt_mode=0, slt_gap=0.3, slt_w=0.07, slt_d=0.06, slt_mat=5, wall_mat=1, frm_mat=1, ground_mat=0, ground_n=1,
             cw_on=0, cw_mw=1.5, cw_mh=0.0, cw_pt=0.06, cw_pd=0.08, cw_band=0.45, cw_edge=0.0,
             cw_bmat=4, cw_mmat=4, cw_fmat=1, cw_finn=0, cw_find=0.35, cw_fint=0.12,
             cw_ht=0.0, cw_hd=0.0, cw_patk=0.6, cw_doorw=0.0, cw_baseh=0.0, cw_caph=0.0, cw_capo=0.0, cw_cornw=0.0,
             cw_prof=0, cw_pat=0, cw_patn=3, cw_basemat=4, cw_capmat=4)
    g.update(p.get("look") or {})
    c = cfg or {}
    fl = int(c.get("flags", 0))
    km = int(c.get("k_mode", 0))
    if km == 1:
        g["cw_on"] = 1
    elif km == 2:
        g["cw_on"] = 0
    if fl & FLAG_CW:
        for k_, a_ in (("cw_mw", "k_mw"), ("cw_mh", "k_mh"), ("cw_pt", "k_pt"), ("cw_pd", "k_pd"), ("cw_band", "k_band"),
                       ("cw_edge", "k_edge"), ("cw_find", "k_find"), ("cw_fint", "k_fint")):
            g[k_] = float(c.get(a_, g[k_]))
        for k_, a_ in (("cw_bmat", "k_bmat"), ("cw_mmat", "k_mmat"), ("cw_fmat", "k_fmat")):
            g[k_] = int(c.get(a_, 0)) or g[k_]
        g["cw_finn"] = int(c.get("k_finn", 0))
        for k_, a_ in (("cw_ht", "k_ht"), ("cw_hd", "k_hd"), ("cw_patk", "k_patk"), ("cw_doorw", "k_doorw"), ("cw_baseh", "k_baseh"),
                       ("cw_caph", "k_caph"), ("cw_capo", "k_capo"), ("cw_cornw", "k_cornw")):
            g[k_] = float(c.get(a_, g[k_]))
        for k_, a_ in (("cw_prof", "k_prof"), ("cw_pat", "k_pat"), ("cw_patn", "k_patn")):
            g[k_] = int(c.get(a_, g[k_]))
        for k_, a_ in (("cw_basemat", "k_basemat"), ("cw_capmat", "k_capmat")):
            g[k_] = int(c.get(a_, 0)) or g[k_]
    if fl & FLAG_PAN:
        g["pan_mode"] = int(c.get("p_mode", 2)) or 2
        g["pan_mat"] = int(c.get("p_mat", 1)) or 1
        g["pan_pad"] = c.get("p_pad", g["pan_pad"])
        g["pan_prob"] = c.get("p_prob", g["pan_prob"])
    if fl & FLAG_CAS:
        g["cas_w"] = c.get("c_w", 0.25)
        g["cas_d"] = c.get("c_d", g["cas_d"])
        g["cas_mat"] = int(c.get("c_mat", 1)) or 1
    if fl & FLAG_SLT:
        g["slt_mode"] = int(c.get("s_mode", 1)) or 1
        g["slt_gap"] = c.get("s_gap", g["slt_gap"])
        g["slt_w"] = c.get("s_w", g["slt_w"])
        g["slt_d"] = c.get("s_d", g["slt_d"])
        g["slt_mat"] = int(c.get("s_mat", 5)) or 5
    return g


def porch_cfg(p, cfg, n, zmin, length):
    """Porche de una pared (dict) o None. Lo propio de la pared pisa lo general."""
    c = cfg or {}
    if int(c.get("flags", 0)) & FLAG_POR:
        if int(c.get("o_on", 2)) != 2:
            return None
        g = dict(PORCH_DEF)
        for k in PORCH_INTS:
            g[k] = int(c.get("o_" + k, PORCH_DEF[k])) or PORCH_DEF[k]
        for k in PORCH_FLOATS:
            g[k] = float(c.get("o_" + k, PORCH_DEF[k]))
        g["d"] = g["d"] or PORCH_DEF["d"]
        g["sz"] = g["sz"] or 1.0
    else:
        gl = p.get("porch")
        if not gl or not gl.get("on") or zmin > 0.05:
            return None
        front = n.y < -0.7
        side = abs(n.x) > 0.7
        mode = int(gl.get("walls", 1))
        if not (front or (mode >= 2 and side) or mode >= 3):
            return None
        g = {k: gl[k] for k in PORCH_DEF if k in gl}
    return g if length >= 1.5 else None


def _bay_plan(p, wid, ops, L, wins, amin, amax, zmin, zmax, fh, mode):
    """Crujías: columnas enteras de otro material que siguen cada columna de ventanas de punta a punta (sobre la planta baja
    si esta lleva otro material). Los huecos de las ventanas de la columna comparten el paño, así sus jambas también cambian.
    {índice de abertura: (material, (a0, a1, z0, z1))}."""
    cols = {}
    for k in wins:
        a0, a1 = ops[k][:2]
        c = cols.setdefault(round(a0, 2), [a0, a1, []])
        c[1] = max(c[1], a1)
        c[2].append(k)
    keys = sorted(cols)
    if not keys:
        return {}
    z0 = zmin + (max(1, int(L.get("ground_n", 1))) * fh if int(L.get("ground_mat", 0)) else 0.0)
    if z0 >= zmax - 0.3:
        return {}
    pad, nk, used, plan = L["pan_pad"], len(keys), 0, {}
    for i, key in enumerate(keys):
        a0, a1, ks = cols[key]
        if mode == 6:
            use = i % 2 == 0
        elif mode == 7:
            use = i % 3 == 0
        elif mode == 8:
            use = i in (0, nk - 1)
        else:
            use = rng_for(p, "bay", wid, i).random() < L["pan_prob"]
        if not use:
            continue
        lo, hi = a0 - pad, a1 + pad
        if i > 0:
            lo = max(lo, (cols[keys[i - 1]][1] + a0) / 2)           # dos crujías vecinas no se pisan
        if i < nk - 1:
            hi = min(hi, (a1 + cols[keys[i + 1]][0]) / 2)
        lo, hi = max(amin, lo), min(amax, hi)
        if hi - lo < 0.2:
            continue
        pm = int(L["pan_mat"])
        if pm == 4:
            name = "facb" if used % 2 == 0 else "facc"
        elif pm == 5:
            name = ("facb", "facc", "facd")[rng_for(p, "baym", wid, i).randrange(3)]
        else:
            name = PAN_MATS.get(pm, "facb")
        used += 1
        for k in ks:
            plan[k] = (MAT[name], (lo, hi, z0, zmax))
    return plan


def panel_plan(p, wid, ops, L, amin, amax, zmin, zmax, fh):
    """Paños de color alrededor de las ventanas: {índice de abertura: (material, (a0, a1, z0, z1))}."""
    mode = int(L["pan_mode"])
    if mode <= 1:
        return {}
    wins = [k for k, op in enumerate(ops) if op[4] == MAT["glass"] and op[1] - op[0] > 0.4]
    if mode >= 6:
        return _bay_plan(p, wid, ops, L, wins, amin, amax, zmin, zmax, fh, mode)
    colkeys = sorted({round(ops[k][0], 2) for k in wins})
    plan = {}
    for k in wins:
        a0, a1, z0, z1 = ops[k][:4]
        fl_i = max(0, int((z0 - zmin) / fh + 1e-3))
        col = colkeys.index(round(a0, 2))
        if mode == 2:
            use = rng_for(p, "pan", wid, fl_i, col).random() < L["pan_prob"]
        elif mode == 3:
            use = (col + fl_i) % 2 == 0
        elif mode == 4:
            use = col % 2 == 0
        else:
            use = True
        if not use:
            continue
        pm = int(L["pan_mat"])
        if pm == 4:
            name = "facb" if (col + fl_i) % 2 == 0 else "facc"
        elif pm == 5:
            name = ("facb", "facc", "facd")[rng_for(p, "panm", wid, fl_i, col).randrange(3)]
        else:
            name = PAN_MATS.get(pm, "facb")
        pad = L["pan_pad"]
        lo, hi = zmin + fl_i * fh, min(zmin + (fl_i + 1) * fh, zmax)
        r = (max(amin, a0 - pad), min(amax, a1 + pad), max(lo, z0 - pad), min(hi, z1 + pad))
        plan[k] = (MAT[name], r)
    return plan


def add_porch(bm, o, u, n, ops, G, lod, amin, amax, zmin, fh):
    G = dict(G)
    _sz = max(0.3, float(G.get("sz", 1.0) or 1.0))
    if abs(_sz - 1.0) > 1e-3:      # tamaño general: escala profundidad, ancho, columnas y extensión
        G["d"] = max(0.8, G["d"] * _sz)
        G["w"] = G["w"] * _sz
        G["ext"] = G["ext"] * _sz
        G["sp"] = max(1.0, G["sp"] * (0.6 + 0.4 * _sz))
        G["ph"] = max(2.2, min(4.5, G["ph"] * (0.75 + 0.25 * _sz)))
    D, dh, ph, ext = G["d"], G["dh"], G["ph"], G["ext"]
    if G["w"] > 0.5:
        c0 = (amin + amax) / 2 + G["off"]
        A0, A1 = c0 - G["w"] / 2, c0 + G["w"] / 2
    else:
        A0, A1 = amin - ext, amax + ext
    L = A1 - A0
    if L < 1.0:
        return
    zb = zmin
    mdeck = MAT[SURF_MATS.get(int(G["dmat"]), "facd")]
    mpost = MAT[SURF_MATS.get(int(G["pmat"]), "trim")]
    lv2 = int(G["lv"]) == 2
    zt0 = zb + dh
    zt1 = zb + fh if lv2 else zt0
    zr = zt1 + ph

    def PT(a, d, z):
        return o + u * a + n * d + Vector((0, 0, z))

    def box(a0, a1, d0, d1, z0, z1, mat, skip=("+y",)):
        if a1 - a0 < 1e-4 or d1 - d0 < 1e-4 or z1 - z0 < 1e-4:
            return
        add_obox(bm, PT((a0 + a1) / 2, (d0 + d1) / 2, (z0 + z1) / 2), u, -n, a1 - a0, d1 - d0, z1 - z0, mat, skip)

    doors = [(op[0] + op[1]) / 2 for op in ops if op[4] == MAT["door"]]
    sa = doors[0] if doors else (A0 + A1) / 2
    sa = min(max(sa, A0 + 0.9), A1 - 0.9) if L > 1.8 else (A0 + A1) / 2
    sw = 1.5
    stairs = int(G["stp"]) == 1 and lod < 2
    # plataforma(s)
    box(A0, A1, 0, D, zb, zt0, mdeck)
    if lv2:
        box(A0, A1, 0, D, zt1 - 0.18, zt1, mdeck)
    # escalones
    if stairs:
        ns = max(1, int(math.ceil(dh / 0.18)))
        for i in range(ns):
            zt_i = zt0 - (i + 1) * dh / ns
            if zt_i > zb + 0.02:
                box(sa - sw / 2, sa + sw / 2, D + i * 0.3, D + (i + 1) * 0.3, zb, zt_i, mdeck)
    if lod >= 2:
        pass
    else:
        # columnas
        cnt = max(2, int(round(L / max(1.0, G["sp"]))) + 1)
        for k in range(cnt):
            a = A0 + 0.1 + (L - 0.2) * k / (cnt - 1)
            if stairs and abs(a - sa) < sw / 2 + 0.05 and cnt > 2:
                continue
            box(a - 0.07, a + 0.07, D - 0.17, D - 0.03, zt0, zr, mpost, skip=())
        if lod == 0:
            box(A0, A1, D - 0.2, D, zr - 0.2, zr, mpost, skip=())
        # barandas
        if int(G["rail"]) > 1 and int(G["enc"]) != 3:
            levels = [(zt0, True)] + ([(zt1, False)] if lv2 else [])
            for zk, ground in levels:
                segs = []
                if stairs and ground:
                    segs += [(A0 + 0.05, sa - sw / 2), (sa + sw / 2, A1 - 0.05)]
                else:
                    segs += [(A0 + 0.05, A1 - 0.05)]
                for a0_, a1_ in segs:
                    if a1_ - a0_ > 0.25:
                        _porch_rail(bm, PT(a0_, D - 0.1, zk), PT(a1_, D - 0.1, zk), int(G["rail"]), lod, MAT["rail"], n)
                for aa in (A0 + 0.07, A1 - 0.07):
                    _porch_rail(bm, PT(aa, 0.05, zk), PT(aa, D - 0.1, zk), int(G["rail"]), lod, MAT["rail"], u)
        # cerramientos (mosquitero / vidriado)
        if lod == 0 and int(G["enc"]) in (2, 3):
            for zk, ztopk in ([(zt0, (zt1 - 0.18) if lv2 else zr)] + ([(zt1, zr)] if lv2 else [])):
                z0g = zk + (0.95 if int(G["enc"]) == 2 else 0.05)
                z1g = ztopk - 0.2
                if z1g - z0g < 0.3:
                    continue
                nb = max(1, int(round(L / 1.1)))
                for b_ in range(nb):
                    a0_, a1_ = A0 + L * b_ / nb, A0 + L * (b_ + 1) / nb
                    _ofa(bm, [PT(a0_ + 0.03, D - 0.1, z0g), PT(a1_ - 0.03, D - 0.1, z0g), PT(a1_ - 0.03, D - 0.1, z1g), PT(a0_ + 0.03, D - 0.1, z1g)], MAT["glass"], n)
                    box(a0_ - 0.02, a0_ + 0.02, D - 0.14, D - 0.06, z0g, z1g, mpost, skip=())
                box(A0, A1, D - 0.14, D - 0.06, z0g - 0.04, z0g, mpost, skip=())
                for aa in (A0 + 0.1, A1 - 0.1):
                    _ofa(bm, [PT(aa, 0.1, z0g), PT(aa, D - 0.1, z0g), PT(aa, D - 0.1, z1g), PT(aa, 0.1, z1g)], MAT["glass"], u if aa > (A0 + A1) / 2 else -u)
    # techo
    kind = int(G["roof"])
    if kind > 1:
        tp = math.tan(math.radians(max(3.0, min(45.0, G["pitch"]))))
        Ae0, Ae1, De = A0 - 0.25, A1 + 0.25, D + 0.3
        zl = zr + 0.1
        half = (Ae1 - Ae0) / 2
        if kind == 3:
            tp = min(tp, 3.2 / max(0.5, half))
        polys = []
        if kind == 2:
            zh = zl + De * tp
            polys.append([(PT(Ae0, De, zl), True), (PT(Ae1, De, zl), True), (PT(Ae1, 0, zh), False), (PT(Ae0, 0, zh), False)])
        elif kind == 3:
            ac = (Ae0 + Ae1) / 2
            rz = zl + half * tp
            polys.append([(PT(Ae0, De, zl), True), (PT(ac, De, rz), True), (PT(ac, 0, rz), False), (PT(Ae0, 0, zl), False)])
            polys.append([(PT(ac, De, rz), True), (PT(Ae1, De, zl), True), (PT(Ae1, 0, zl), False), (PT(ac, 0, rz), False)])
            _ofa(bm, [PT(Ae0 + 0.05, De - 0.05, zl), PT(Ae1 - 0.05, De - 0.05, zl), PT(ac, De - 0.05, rz - 0.05)], MAT["facade"], n)
        else:
            Dx = max(0.3, min(De, half - 0.05))
            zh = zl + De * tp
            polys.append([(PT(Ae0, De, zl), True), (PT(Ae1, De, zl), True), (PT(Ae1 - Dx, 0, zh), False), (PT(Ae0 + Dx, 0, zh), False)])
            polys.append([(PT(Ae0, De, zl), True), (PT(Ae0 + Dx, 0, zh), False), (PT(Ae0, 0, zl), False)])
            polys.append([(PT(Ae1, De, zl), True), (PT(Ae1, 0, zl), False), (PT(Ae1 - Dx, 0, zh), False)])
        _roof_surface(bm, polys, {"t": 0.1, "ribs": 0, "rib_h": 0.0, "rib_gap": 0.2}, lod, zl)


def _porch_rail(bm, p0, p1, kind, lod, mat, ref):
    """Baranda recta entre dos puntos (kind: 2 balaustres, 3 sólida, 4 barras, 5 vidrio)."""
    zv = Vector((0, 0, 1))
    d = p1 - p0
    L = d.length
    if L < 0.1:
        return
    d = d / L
    ay = zv.cross(d)
    mid = (p0 + p1) / 2
    def seg(zc, th, h, length=L, off=0.0, mat_=mat):
        add_obox(bm, mid + d * off + Vector((0, 0, zc)), d, ay, length, th, h, mat_)
    if lod >= 1 or kind == 3:
        seg(0.475, 0.07, 0.95)
        return
    seg(0.95, 0.09, 0.06)
    if kind == 2:
        seg(0.08, 0.06, 0.06)
        nb = min(80, max(1, int(L / 0.2)))
        for k in range(nb):
            seg(0.5, 0.03, 0.82, 0.03, (k + 0.5) / nb * L - L / 2)
    elif kind == 4:
        for zc in (0.3, 0.55, 0.8):
            seg(zc, 0.03, 0.03)
        seg(0.1, 0.05, 0.1)
    else:
        seg(0.08, 0.06, 0.06)
        q = [p0 + zv * 0.1, p1 + zv * 0.1, p1 + zv * 0.92, p0 + zv * 0.92]
        _ofa(bm, q, MAT["glass"], ay)


def _add_slats(bm, o, u, n, poly, ops, L, amin, amax, zmin, zmax, fh):
    gap, w, d = max(0.1, L["slt_gap"]), L["slt_w"], L["slt_d"]
    mat = MAT[SURF_MATS.get(int(L["slt_mat"]), "facd")]
    ay = -n
    cap = 400
    boxes = 0
    if int(L["slt_mode"]) == 1:
        a = amin + gap / 2
        while a < amax - gap / 4 and boxes < cap:
            a0, a1 = a - w / 2, a + w / 2
            blk = sorted((max(zmin, op[2] - 0.03), min(zmax, op[3] + 0.03)) for op in ops
                         if not (op[1] <= a0 - 0.05 or op[0] >= a1 + 0.05))
            cur = zmin
            segs = []
            for b0, b1 in blk:
                if b0 > cur:
                    segs.append((cur, b0))
                cur = max(cur, b1)
            if cur < zmax:
                segs.append((cur, zmax))
            for z0, z1 in segs:
                if z1 - z0 > 0.2 and rect_fits(poly, a0, a1, z0, z1):
                    add_obox(bm, o + u * a + n * (d / 2) + Vector((0, 0, (z0 + z1) / 2)), u, ay, w, d, z1 - z0, mat, skip=("+y",))
                    boxes += 1
            a += gap
    else:
        z = zmin + gap / 2
        while z < zmax - gap / 4 and boxes < cap:
            z0, z1 = z - w / 2, z + w / 2
            for s0, e0 in poly_intervals(poly, z):
                cuts = [(max(s0, op[0] - 0.05), min(e0, op[1] + 0.05)) for op in ops if op[2] < z1 and op[3] > z0]
                cuts = sorted(c for c in cuts if c[0] < c[1])
                cur = s0
                segs = []
                for c0, c1 in cuts:
                    if c0 > cur:
                        segs.append((cur, c0))
                    cur = max(cur, c1)
                if cur < e0:
                    segs.append((cur, e0))
                for q0, q1 in segs:
                    if q1 - q0 > 0.15:
                        add_obox(bm, o + u * ((q0 + q1) / 2) + n * (d / 2) + Vector((0, 0, z)), u, ay, q1 - q0, d, w, mat, skip=("+y",))
                        boxes += 1
            z += gap


def _grid_cells(A, Z, rects):
    """Celdas (i, j) de la grilla de cortes A x Z cubiertas por cada rectángulo (key, a0, a1, z0, z1).
    Si dos rectángulos se superponen gana el primero. Tolerancia 1e-4, igual que la comparación directa."""
    cells = {}
    nA, nZ = len(A), len(Z)
    for key, a0, a1, z0, z1 in rects:
        ia0, ia1 = bisect_left(A, a0 - 1e-4) + 1, min(bisect_right(A, a1 + 1e-4) - 1, nA - 1)
        iz0, iz1 = bisect_left(Z, z0 - 1e-4) + 1, min(bisect_right(Z, z1 + 1e-4) - 1, nZ - 1)
        for i in range(ia0, ia1 + 1):
            for j in range(iz0, iz1 + 1):
                cells.setdefault((i, j), key)
    return cells


def build_curtain(bm, pts, n, p, wid, lod, cfg, L):
    """Muro cortina de una pared: cuadrícula de vidrio (malla _Win) con perfiles, banda opaca por piso,
    margen opaco a los lados y aletas verticales. LOD1: una tira de vidrio por piso; LOD2: un solo vidrio."""
    zax = Vector((0, 0, 1))
    u = zax.cross(n).normalized()
    v0 = pts[0]
    o = Vector((v0.x, v0.y, 0.0))
    poly = [((q - v0).dot(u), q.z) for q in pts]
    zmin, zmax = min(q[1] for q in poly), max(q[1] for q in poly)
    amin, amax = min(q[0] for q in poly), max(q[0] for q in poly)
    fh = p["floor_h"]
    fmi = int((cfg or {}).get("f_mat", 0)) or int(L.get("wall_mat", 1))
    base_mat = (MAT["facade"], MAT["facade"], MAT["facb"], MAT["facc"], MAT["facd"])[min(max(fmi, 1), 4)]

    def mat_of(i):
        m = MAT[SURF_MATS.get(int(i), "trim")]
        return base_mat if m == MAT["facade"] else m
    bmat, mmat, fmat = mat_of(L["cw_bmat"]), mat_of(L["cw_mmat"]), mat_of(L["cw_fmat"])
    basemat, capmat = mat_of(L.get("cw_basemat", 4)), mat_of(L.get("cw_capmat", 4))
    mw, mh = max(0.4, float(L["cw_mw"])), max(0.0, float(L["cw_mh"]))
    pt, pd = max(0.0, float(L["cw_pt"])), max(0.02, float(L["cw_pd"]))
    band, edge = max(0.0, float(L["cw_band"])), max(0.0, float(L["cw_edge"]))
    fn, fd, ft = int(L["cw_finn"]), max(0.0, float(L["cw_find"])), max(0.0, float(L["cw_fint"]))
    prof, pat = int(L.get("cw_prof", 0)), int(L.get("cw_pat", 0))
    patn, patk = max(2, int(L.get("cw_patn", 3))), min(1.0, max(0.2, float(L.get("cw_patk", 0.6))))
    htt = float(L.get("cw_ht", 0.0)) or pt
    hdd = float(L.get("cw_hd", 0.0)) or pd
    doorw, base_h = max(0.0, float(L.get("cw_doorw", 0.0))), max(0.0, float(L.get("cw_baseh", 0.0)))
    cap_h, capo = max(0.0, float(L.get("cw_caph", 0.0))), max(0.0, float(L.get("cw_capo", 0.0)))
    cornw = max(0.0, float(L.get("cw_cornw", 0.0)))
    if prof == 3:      # vidrio estructural: sin perfiles a la vista
        pt = htt = 0.0
    ay = -n
    GL = -0.05      # el vidrio queda apenas hacia adentro de las bandas y los perfiles sobresalen

    def P(a, z, off=0.0):
        return o + u * a + n * off + Vector((0, 0, z))

    def quad(a0, a1, z0, z1, off, mat):
        try:
            bm.faces.new([bm.verts.new(P(a, z, off)) for a, z in ((a0, z0), (a1, z0), (a1, z1), (a0, z1))]).material_index = mat
        except ValueError:
            pass

    def opaque(a0, a1, z0, z1, mat, off=0.0):
        """Parte llena: el rectángulo recortado por el contorno de la pared."""
        if a1 - a0 < 1e-3 or z1 - z0 < 1e-3:
            return
        if rect_fits(poly, a0, a1, z0, z1, 0.0):
            quad(a0, a1, z0, z1, off, mat)
            return
        c = clip_poly(clip_poly(clip_poly(clip_poly(poly, 0, a0, True), 0, a1, False), 1, z0, True), 1, z1, False)
        if len(c) >= 3 and poly_area(c) > 0.01:
            try:
                bm.faces.new([bm.verts.new(P(a, z, off)) for a, z in c]).material_index = mat
            except ValueError:
                pass

    length = amax - amin
    if length < 1.2 or zmax - zmin < 0.5:
        opaque(amin, amax, zmin, zmax, bmat)
        return
    if edge > 0 and length - 2 * edge < max(0.8, mw * 0.8):
        edge = 0.0
    a_l, a_r = amin + edge, amax - edge
    ncols = max(1, int(round((a_r - a_l) / mw)))
    wts = [1.0 if (c % 2 == 0 or pat != 1) else patk for c in range(ncols)]     # módulos angostos y anchos alternados
    ce = [a_l]
    for w_ in wts:
        ce.append(ce[-1] + (a_r - a_l) * w_ / sum(wts))
    levels = [zmin] + [round(k * fh, 4) for k in range(int(zmin // fh) + 1, int(zmax // fh) + 1)
                       if zmin + 0.05 < k * fh < zmax - 0.05] + [zmax]
    glass = MAT["glass"]
    top_band = min(band, fh * 0.6) if band > 0.02 else 0.0
    # remates: zócalo abajo y antepecho o cornisa arriba (solo si la pared tiene alto de sobra)
    base_h = min(base_h, fh * 0.8) if base_h > 0.05 and zmax - zmin > 1.2 else 0.0
    cap_h = min(cap_h, fh * 0.8) if cap_h > 0.05 and zmax - zmin > 1.2 else 0.0
    if cap_h:
        levels[-1] = zmax - cap_h
        top_band = 0.0 if lod >= 2 else top_band

    if edge > 0:
        opaque(amin, a_l, zmin, zmax, bmat)
        opaque(a_r, amax, zmin, zmax, bmat)
    if base_h:
        opaque(amin, amax, zmin, zmin + base_h, basemat, 0.01)
    if cap_h:
        if capo > 0.02 and rect_fits(poly, amin, amax, zmax - cap_h, zmax, 0.0) and lod <= 1:
            add_obox(bm, P((amin + amax) / 2, zmax - cap_h / 2, capo / 2), u, ay, amax - amin, capo, cap_h, capmat, skip=("+y",))
        else:
            opaque(amin, amax, zmax - cap_h, zmax, capmat, 0.01)

    def vbox(a, w, d0, d1, z0, z1, mat):
        add_obox(bm, P(a, (z0 + z1) / 2, (d0 + d1) / 2), u, ay, w, d1 - d0, z1 - z0, mat, skip=("+y", "-z"))

    if cornw > 0.02 and lod <= 1:       # perfiles gruesos en las esquinas de la pared
        for a in (amin + cornw / 2, amax - cornw / 2):
            if rect_fits(poly, a - cornw / 2, a + cornw / 2, zmin, zmax, 0.0):
                vbox(a, cornw, GL, max(pd, 0.1) + 0.03, zmin, zmax, mmat)

    if lod >= 2:      # el vidrio es el frente; la banda de arriba queda como pared
        zg0, zg1 = zmin + base_h, zmax - (cap_h or top_band)
        if rect_fits(poly, a_l, a_r, zg0, zg1, 0.0):
            quad(a_l, a_r, zg0, zg1, 0.0, glass)
            if top_band > 0:
                opaque(a_l, a_r, zg1, zmax, bmat)
        else:
            opaque(a_l, a_r, zg0, zg1, bmat)
        return

    def runs(a, w):
        """Tramos verticales (z0, z1) donde una columna de ancho w cabe dentro de la pared."""
        out, cur = [], None
        for i in range(len(levels) - 1):
            z0, z1 = levels[i], levels[i + 1]
            if rect_fits(poly, a - w / 2, a + w / 2, z0, z1, 0.0):
                cur = [cur[0], z1] if cur else [z0, z1]
            elif cur:
                out.append(tuple(cur))
                cur = None
        if cur:
            out.append(tuple(cur))
        return out

    hl = set()
    inset = 0.0 if prof == 3 else max(pt, 0.03) / 2
    for i in range(len(levels) - 1):
        z0, z1 = levels[i], levels[i + 1]
        if i == 0 and base_h:
            z0 = z0 + base_h
        b = top_band if (top_band > 0 and z1 - z0 > top_band + 0.5) else 0.0
        gz0, gz1 = z0, z1 - b
        if b:
            opaque(a_l, a_r, gz1, z1, bmat)
        if gz1 - gz0 < 0.3:
            opaque(a_l, a_r, gz0, gz1, bmat)
            continue
        if lod == 1:     # una tira de vidrio por piso
            if rect_fits(poly, a_l, a_r, gz0, gz1, 0.0):
                quad(a_l, a_r, gz0, gz1, 0.03, glass)
            else:
                opaque(a_l, a_r, gz0, gz1, bmat)
            continue
        nr = max(1, int(round((gz1 - gz0) / mh))) if mh > 0.2 else 1
        rh = (gz1 - gz0) / nr
        zb = [gz0 + j * rh for j in range(nr + 1)]
        for j in range(1, nr):
            hl.add(round(zb[j], 4))
        hl.add(round(gz1, 4))
        if i > 0 or base_h:
            hl.add(round(gz0, 4))
        for c in range(ncols):
            ca0, ca1 = ce[c], ce[c + 1]
            ia0 = ca0 + (inset if (c > 0 or edge > 0) else 0.0)
            ia1 = ca1 - (inset if (c < ncols - 1 or edge > 0) else 0.0)
            panel = pat == 2 and (c % patn) == patn - 1       # paño lleno en vez de vidrio
            for j in range(nr):
                za0 = zb[j] + (inset if (j > 0 or i > 0 or base_h) else 0.0)
                za1 = zb[j + 1] - inset
                if not panel and rect_fits(poly, ia0, ia1, za0, za1, 0.0):
                    quad(ia0, ia1, za0, za1, GL, glass)
                else:
                    opaque(ca0, ca1, zb[j], zb[j + 1], bmat)

    def round_post(a, d_out, r, z0, z1, mat, horizontal=False, a1=None):
        """Perfil de sección redondeada (prisma de 8 lados), vertical u horizontal."""
        if horizontal:
            ang = math.atan2(u.y, u.x)
            mat_ = Matrix.Translation(P((a + a1) / 2, z0, d_out)) @ Matrix.Rotation(ang, 4, "Z") @ Matrix.Rotation(math.pi / 2, 4, "Y")
            depth_ = a1 - a
        else:
            mat_ = Matrix.Translation(P(a, (z0 + z1) / 2, d_out))
            depth_ = z1 - z0
        res = bmesh.ops.create_cone(bm, cap_ends=False, cap_tris=False, segments=8, radius1=r, radius2=r, depth=depth_, matrix=mat_)
        for v in res["verts"]:
            for f in v.link_faces:
                f.material_index = mat

    if lod == 0 and pt > 0.005:       # perfiles: verticales (largo completo) y horizontales
        for c in range(0 if edge > 0 else 1, ncols + (1 if edge > 0 else 0)):
            a = ce[c]
            for z0, z1 in runs(a, pt):
                if prof == 2:
                    round_post(a, (GL + pd) / 2, pt / 2 if pt < pd - GL else (pd - GL) / 2, z0, z1, mmat)
                else:
                    vbox(a, pt, GL, pd, z0, z1, mmat)
                    if prof == 1:        # tapa plana: placa fina y ancha al frente
                        vbox(a, pt * 2.4, pd, pd + 0.025, z0, z1, mmat)
        for z in sorted(hl):
            zz = min(max(z, zmin + 1e-3), zmax - 1e-3)
            for s0, e0 in poly_intervals(poly, zz):
                q0, q1 = max(s0, a_l), min(e0, a_r)
                if q1 - q0 > 0.2:
                    if prof == 2:
                        round_post(q0, (GL + hdd) / 2, htt / 2 if htt < hdd - GL else (hdd - GL) / 2, z, z, mmat, True, q1)
                    else:
                        add_obox(bm, P((q0 + q1) / 2, z, (GL + hdd) / 2), u, ay, q1 - q0, hdd - GL, htt, mmat, skip=("+y", "+x", "-x"))
                        if prof == 1:
                            add_obox(bm, P((q0 + q1) / 2, z, hdd + 0.0125), u, ay, q1 - q0, 0.025, htt * 2.4, mmat, skip=("+y", "+x", "-x"))
    if fn > 0 and fd > 0.02 and ft > 0.02:   # aletas verticales
        for c in range(fn, ncols, fn):
            a = ce[c]
            for z0, z1 in runs(a, ft):
                vbox(a, ft, 0.0, fd, z0, z1, fmat)

    if lod == 0 and doorw > 0.3 and length > doorw + 1.0:     # puerta de acceso de vidrio en el centro de la planta baja
        ac = (a_l + a_r) / 2
        dh = min(2.4, max(1.8, levels[1] - zmin - 0.25)) if len(levels) > 1 else 2.2
        kick = max(0.25, base_h)
        if rect_fits(poly, ac - doorw / 2 - 0.05, ac + doorw / 2 + 0.05, zmin, zmin + dh + 0.08, 0.0):
            for sx in (-1, 1):
                vbox(ac + sx * (doorw / 2 + 0.04), 0.08, GL, 0.1, zmin, zmin + dh + 0.07, mmat)
            add_obox(bm, P(ac, zmin + dh + 0.035, (GL + 0.1) / 2), u, ay, doorw + 0.16, 0.1 - GL, 0.07, mmat, skip=("+y", "-z"))
            vbox(ac, 0.05, GL, 0.09, zmin, zmin + dh, mmat)
            for sx in (-1, 1):
                a0_, a1_ = (ac - doorw / 2, ac - 0.025) if sx < 0 else (ac + 0.025, ac + doorw / 2)
                quad(a0_, a1_, zmin + kick, zmin + dh, -0.03, glass)
                quad(a0_, a1_, zmin, zmin + kick, 0.02, MAT["door"])


def _wall_is_rect(pts, v0, u, amin, amax, zmin, zmax):
    """Contorno de una pared rectangular vertical: lista [(a, z)] de sus vértices (las esquinas y los que la forma
    ya tiene sobre los bordes), o None si no es un rectángulo."""
    if len(pts) < 4 or amax - amin < 1e-3 or zmax - zmin < 1e-3:
        return None
    out, corners = [], set()
    for q in pts:
        a, z = (q - v0).dot(u), q.z
        ea, ez = min(abs(a - amin), abs(a - amax)), min(abs(z - zmin), abs(z - zmax))
        if ea > 1e-4 and ez > 1e-4:
            return None                      # un vértice dentro del rectángulo: no es rectangular
        out.append((round(a, 4), round(z, 4)))
        if ea <= 1e-4 and ez <= 1e-4:
            corners.add((round(a, 3), round(z, 3)))
    return out if len(corners) == 4 else None


def _wall_pieces(amin, amax, zmin, zmax, ops, pans, split_z=None):
    """Descompone una pared rectangular con aberturas en el mínimo de rectángulos.

    Devuelve (huecos [(k, a0, a1, z0, z1)], piezas [(a0, a1, z0, z1, paño o None)]). Cada abertura es un solo
    rectángulo. El resto se corta en columnas solo en los bordes de las aberturas (y paños) y las columnas vecinas
    con el mismo tramo libre se unen: a los costados de una ventana queda una tira de alto completo y solo arriba y
    abajo de ella hay piezas cortas. `split_z`: línea de piso que separa dos materiales (solo en lo libre)."""
    r4 = lambda v: round(v, 4)
    holes = []
    for k, op in enumerate(ops):
        a0, a1, z0, z1 = max(amin, op[0]), min(amax, op[1]), max(zmin, op[2]), min(zmax, op[3])
        if a1 - a0 > 1e-4 and z1 - z0 > 1e-4:
            holes.append((k, r4(a0), r4(a1), r4(z0), r4(z1)))
    pl = []
    for k, (_m, r) in pans.items():
        a0, a1, z0, z1 = max(amin, r[0]), min(amax, r[1]), max(zmin, r[2]), min(zmax, r[3])
        if a1 - a0 > 1e-4 and z1 - z0 > 1e-4:
            pl.append((k, r4(a0), r4(a1), r4(z0), r4(z1)))
    xs = sorted({r4(amin), r4(amax)} | {h[1] for h in holes} | {h[2] for h in holes}
                | {q[1] for q in pl} | {q[2] for q in pl})
    free, open_ = [], {}
    zlo, zhi = r4(zmin), r4(zmax)
    for i in range(len(xs) - 1):
        x0, x1 = xs[i], xs[i + 1]
        if x1 - x0 < 1e-4:
            continue
        xm = (x0 + x1) / 2
        cov = sorted((h[3], h[4]) for h in holes if h[1] <= xm <= h[2])
        ivs, z = [], zlo
        for c0, c1 in cov:
            if c0 > z + 1e-4:
                ivs.append((z, c0))
            z = max(z, c1)
        if zhi > z + 1e-4:
            ivs.append((z, zhi))
        pcol = [q for q in pl if q[1] <= xm <= q[2]]
        cur = set()
        for a, b in ivs:
            cuts = {a, b}
            for q in pcol:
                for zz in (q[3], q[4]):
                    if a + 1e-4 < zz < b - 1e-4:
                        cuts.add(zz)
            if split_z is not None and a + 1e-4 < split_z < b - 1e-4:
                cuts.add(r4(split_z))
            cs = sorted(cuts)
            for c0, c1 in zip(cs, cs[1:]):
                zm = (c0 + c1) / 2
                cur.add((c0, c1, next((q[0] for q in pcol if q[3] <= zm <= q[4]), None)))
        for key in list(open_):
            if key not in cur:
                free.append((open_.pop(key), x0) + key)
        for key in cur:
            open_.setdefault(key, x0)
    for key, a0 in open_.items():
        free.append((a0, xs[-1]) + key)
    return holes, free


def _wall_rect_faces(tmp, o, u, n, rects, extra=()):
    """Crea las caras de los rectángulos [(a0, a1, z0, z1, etiqueta)] compartiendo vértices. Si la esquina de un
    rectángulo vecino cae sobre un borde, esa esquina se agrega como vértice alineado: así las caras quedan
    conectadas (sin cortes en T) aunque la tira lateral no esté partida. Devuelve [(etiqueta, cara)]."""
    at_z, at_x = {}, {}
    for a, z in extra:                       # vértices que la forma ya tenía sobre el borde (los comparten las paredes vecinas)
        at_z.setdefault(z, set()).add(a)
        at_x.setdefault(a, set()).add(z)
    for a0, a1, z0, z1, _t in rects:
        for a in (a0, a1):
            for z in (z0, z1):
                at_z.setdefault(z, set()).add(a)
                at_x.setdefault(a, set()).add(z)
    at_z = {k: sorted(v) for k, v in at_z.items()}
    at_x = {k: sorted(v) for k, v in at_x.items()}
    verts = {}

    def V(a, z):
        v = verts.get((a, z))
        if v is None:
            v = verts[(a, z)] = tmp.verts.new(o + u * a + Vector((0.0, 0.0, z)))
        return v

    out = []
    for a0, a1, z0, z1, tag in rects:
        ring = [(a0, z0)] + [(a, z0) for a in at_z[z0] if a0 < a < a1] + [(a1, z0)]
        ring += [(a1, z) for z in at_x[a1] if z0 < z < z1] + [(a1, z1)]
        ring += [(a, z1) for a in reversed(at_z[z1]) if a0 < a < a1] + [(a0, z1)]
        ring += [(a0, z) for z in reversed(at_x[a0]) if z0 < z < z1]
        try:
            f = tmp.faces.new([V(a, z) for a, z in ring])
        except ValueError:
            continue
        out.append((tag, f))
    tmp.normal_update()
    for _t, f in out:
        if f.normal.dot(n) < 0:
            f.normal_flip()
    return out


def _wall_sink_openings(tmp, groups, ext, skip_back, n, p, cfg, ops, depths, pans, cut, styon):
    """Hunde cada abertura (extruye su cara hacia adentro) y asigna materiales a las caras resultantes."""
    for k, faces in groups.items():
        mat = ops[k][4]
        depth = depths[k] if cut else 0.0
        has_style = styon(k, ops[k]) and mat != MAT["facade"] and style_for(p, mat, cfg, ops[k]) is not None
        if depth > 0:
            res = bmesh.ops.extrude_face_region(tmp, geom=faces)
            newv = [g for g in res["geom"] if isinstance(g, bmesh.types.BMVert)]
            bmesh.ops.translate(tmp, vec=-n * depth, verts=newv)
            ext += [(g, k) for g in res["geom"] if isinstance(g, bmesh.types.BMFace)]
            # la cara original quedaba tapando el hueco (con la normal invertida) y el
            # vidrio no se veía desde afuera: se borra para abrir la ventana de verdad
            old = [fc for fc in faces if fc.is_valid]
            if old:
                bmesh.ops.delete(tmp, geom=old, context="FACES_ONLY")
        else:
            for fc in faces:
                fc.material_index = MAT["facade"] if has_style else mat
        if depth > 0 and has_style:
            st_ = style_for(p, mat, cfg, ops[k])
            if st_.get("kind") == "window" and st_.get("shape", "RECT") == "RECT":   # el fondo lo tapa el vidrio del estilo: no se genera
                skip_back.add(k)
    if ext:
        tmp.normal_update()
        drop = []
        for g, k in ext:
            if g.normal.dot(n) > 0.9 and k in skip_back:
                drop.append(g)
                continue
            if g.normal.dot(n) > 0.9:
                g.material_index = (MAT["facade"] if (styon(k, ops[k]) and (pieces_for(p, ops[k][4]) or style_for(p, ops[k][4], cfg, ops[k])))
                                    else ops[k][4])
            else:
                g.material_index = pans[k][0] if k in pans else MAT["facade"]
        if drop:
            bmesh.ops.delete(tmp, geom=drop, context="FACES_ONLY")


def _wall_cut(bm, pts, n, p, cfg, ops, depths, pans, cut, o, u, v0, zmin, zmax, amin, amax, fh, base_mat, styon,
              ground_mat=None, ground_h=0.0):
    """Corta la pared en grilla por las aberturas, hunde las que tienen profundidad y copia el resultado a `bm`.
    Devuelve False si la cara de la pared no se pudo crear."""
    zax = Vector((0, 0, 1))
    tmp = bmesh.new()
    outline = _wall_is_rect(pts, v0, u, amin, amax, zmin, zmax) if ((ops and cut) or pans) else None
    rect_wall = outline is not None
    if not rect_wall:
        try:
            f0 = tmp.faces.new([tmp.verts.new(q) for q in pts])
        except ValueError:
            tmp.free()
            return False
        f0.material_index = MAT["facade"]
        if ground_mat is not None and not (ops and cut) and zmin + 1e-3 < ground_h < zmax - 1e-3:
            _bisect(tmp, Vector((0, 0, ground_h)), zax)      # sin aberturas cortadas, la línea del piso se corta igual

    if rect_wall:
        # pared rectangular: el mínimo de caras. Cada abertura es una sola cara; el resto se arma con tiras que solo
        # se cortan donde hace falta (laterales de alto completo, arriba y abajo de cada abertura)
        holes, free = _wall_pieces(amin, amax, zmin, zmax, ops if cut else [], pans, ground_h if ground_mat is not None else None)
        rects = [(h[1], h[2], h[3], h[4], ("h", h[0])) for h in holes] + [(f[0], f[1], f[2], f[3], ("f", f[4])) for f in free]
        made = _wall_rect_faces(tmp, o, u, n, rects, outline)
        groups = {}
        for tag, fc in made:
            if tag[0] == "h":
                groups.setdefault(tag[1], []).append(fc)
            elif tag[1] is not None and tag[1] in pans:
                fc.material_index = pans[tag[1]][0]
            else:
                fc.material_index = MAT["facade"]
        ext = []
        skip_back = set()
        _wall_sink_openings(tmp, groups, ext, skip_back, n, p, cfg, ops, depths, pans, cut, styon)
    elif ops and cut:  # cortes: bordes de aberturas y líneas de piso (globales, así las paredes vecinas coinciden)
        a_cuts = sorted({round(a, 4) for op in ops for a in op[:2]})
        z_vals = {round(k * fh, 4) for k in range(1, int(zmax // fh) + 1)}
        z_vals |= {round(z, 4) for op in ops for z in op[2:4]}
        for _m, r in pans.values():
            a_cuts = sorted(set(a_cuts) | {round(r[0], 4), round(r[1], 4)})
            z_vals |= {round(r[2], 4), round(r[3], 4)}
        for a in a_cuts:
            if amin + 1e-3 < a < amax - 1e-3:
                _bisect(tmp, o + u * a, u)
        for z in sorted(z_vals):
            if zmin + 1e-3 < z < zmax - 1e-3:
                _bisect(tmp, Vector((0, 0, z)), zax)

        # cada cara se ubica en la grilla de cortes (búsqueda binaria) en vez de recorrer todas las aberturas
        A_ = sorted({round(a, 4) for op in ops for a in op[:2]} | {round(r[i], 4) for _m, r in pans.values() for i in (0, 1)})
        Z_ = sorted(z_vals)
        op_cells = _grid_cells(A_, Z_, [(k, op[0], op[1], op[2], op[3]) for k, op in enumerate(ops)])
        pan_cells = _grid_cells(A_, Z_, [(k, r[0], r[1], r[2], r[3]) for k, (_m, r) in pans.items()]) if pans else {}
        groups = {}
        loose = []
        for fc in tmp.faces:
            c = fc.calc_center_median()
            a, z = (c - v0).dot(u), c.z
            k = op_cells.get((bisect_right(A_, a), bisect_right(Z_, z)))
            if k is None:
                loose.append((fc, a, z))
            else:
                groups.setdefault(k, []).append(fc)
        for fc, a, z in loose if pans else ():
            k = pan_cells.get((bisect_right(A_, a), bisect_right(Z_, z)))
            if k is not None:
                fc.material_index = pans[k][0]
        ext = []
        skip_back = set()
        _wall_sink_openings(tmp, groups, ext, skip_back, n, p, cfg, ops, depths, pans, cut, styon)

    tmp.verts.index_update()
    vmap = {}
    for fc in tmp.faces:
        vs = []
        for v in fc.verts:
            nv = vmap.get(v.index)
            if nv is None:
                nv = vmap[v.index] = bm.verts.new(v.co)
            vs.append(nv)
        try:
            mi = fc.material_index
            if mi == MAT["facade"]:
                mi = ground_mat if (ground_mat is not None and fc.calc_center_median().z < ground_h - 1e-3) else base_mat
            bm.faces.new(vs).material_index = mi
        except ValueError:
            pass
    tmp.free()
    return True


def _piece_tris(bm, skip=()):
    return sum(len(f.verts) - 2 for f in bm.faces if f.material_index not in skip)


def _reduce_piece(sb, ratio, n_out):
    """Reduce una ventana o puerta armada con todo su detalle (bmesh suelto) a `ratio` de sus triángulos, sin contar el vidrio
    (que va en otra malla). Orden: se borra lo que mira hacia la pared, se unen las caras coplanares, se quitan las piezas
    más chicas (travesaños, barrotes, adornos) y, si todavía sobra, se simplifican las curvas. Devuelve (antes, después)."""
    skip = SUB_MATS
    bmesh.ops.remove_doubles(sb, verts=sb.verts[:], dist=1e-4)
    sb.normal_update()
    before = _piece_tris(sb, skip)
    target = int(before * ratio)
    dead = [f for f in sb.faces if f.material_index not in skip and f.normal.dot(n_out) < -0.7]
    if dead:
        bmesh.ops.delete(sb, geom=dead, context="FACES")
    if _piece_tris(sb, skip) > target:
        bmesh.ops.dissolve_limit(sb, angle_limit=math.radians(1.0), verts=sb.verts[:], edges=sb.edges[:],
                                 use_dissolve_boundaries=False, delimit={"MATERIAL"})
    if _piece_tris(sb, skip) > target:    # piezas sueltas: de menor a mayor tamaño, siempre queda la más grande
        sb.faces.index_update()
        seen, parts = set(), []
        for f in sb.faces:
            if f.index in seen or f.material_index in skip:
                continue
            stack, cur = [f], []
            seen.add(f.index)
            while stack:
                g = stack.pop()
                cur.append(g)
                for v in g.verts:
                    for h in v.link_faces:
                        if h.index not in seen and h.material_index not in skip:
                            seen.add(h.index)
                            stack.append(h)
            co = [v.co for g in cur for v in g.verts]
            diag = (Vector((max(c.x for c in co), max(c.y for c in co), max(c.z for c in co)))
                    - Vector((min(c.x for c in co), min(c.y for c in co), min(c.z for c in co)))).length
            parts.append((diag, cur))
        parts.sort(key=lambda t: t[0])
        tris = _piece_tris(sb, skip)
        for diag, cur in parts[:-1]:
            if tris <= target:
                break
            tris -= sum(len(g.verts) - 2 for g in cur)
            bmesh.ops.delete(sb, geom=[g for g in cur if g.is_valid], context="FACES")
    for ang in (5.0, 12.0, 25.0, 40.0):    # curvas: menos segmentos
        if _piece_tris(sb, skip) <= target:
            break
        bmesh.ops.dissolve_limit(sb, angle_limit=math.radians(ang), verts=sb.verts[:], edges=sb.edges[:],
                                 use_dissolve_boundaries=False, delimit={"MATERIAL"})
    if _piece_tris(sb, skip) > target:    # último recurso: los cantos (caras de costado) más chicos, que casi no se ven de lejos
        sides = sorted((f for f in sb.faces if f.material_index not in skip and abs(f.normal.dot(n_out)) < 0.3),
                       key=lambda f: f.calc_area())
        tris = _piece_tris(sb, skip)
        dead = []
        for f in sides:
            if tris <= target:
                break
            tris -= len(f.verts) - 2
            dead.append(f)
        if dead:
            bmesh.ops.delete(sb, geom=dead, context="FACES")
    return before, _piece_tris(sb, skip)


def _merge_piece(bm, sb):
    sb.verts.index_update()
    vm = {}
    for f in sb.faces:
        vs = []
        for v in f.verts:
            nv = vm.get(v.index)
            if nv is None:
                nv = vm[v.index] = bm.verts.new(v.co)
            vs.append(nv)
        try:
            bm.faces.new(vs).material_index = f.material_index
        except ValueError:
            pass


def _wall_lod1_styled(bm, sty, o, u, n, p, ratio=0.5):
    """LOD1 por componente: cada ventana o puerta con estilo se arma con su detalle real y se reduce a la mitad de triángulos."""
    for op, st in sty:
        sb = bmesh.new()
        try:
            insert_style(sb, st, o, u, n, op, 0.0, p)
            if not sb.faces:
                continue
            _reduce_piece(sb, ratio, n)
            _merge_piece(bm, sb)
        finally:
            sb.free()


def _wall_lod_quads(bm, qops, ops_q, cut, p, cfg, L, o, u, n, wid, lod, cs1, zmin):
    """LOD1/LOD2 sin aberturas cortadas: cada abertura es un cuadro apenas separado de la pared, con marco, alféizar y dintel."""
    if lod >= 1 and (not cut or ops_q):  # pared entera y cada abertura como un cuadro apenas separado (2 triángulos)
        off = 0.04 if lod == 1 else 0.05   # en LOD2 la ventana flota sobre el cuerpo simplificado
        if cs1:
            off = 0.015
        tier, q = p.get("lod1_tier", 0), float(p.get("_q", 1.0))
        for a0, a1, z0, z1, mat in qops:
            qmat = mat
            if cs1 and p.get("_cs1_nowin") and mat == MAT["glass"]:
                continue       # LOD de CS1 sin presupuesto para ventanas
            if mat == MAT["door"]:      # un portón de garaje usa el material de portones, como en el LOD0
                _ds = style_for(p, MAT["door"], cfg)
                if _ds and _ds.get("garage"):
                    qmat = MAT["gar"]
            qv = [bm.verts.new(o + u * a + n * off + Vector((0, 0, z))) for a, z in ((a0, z0), (a1, z0), (a1, z1), (a0, z1))]
            try:
                bm.faces.new(qv).material_index = qmat
            except ValueError:
                pass
            if cs1:
                # CS1: el marco interior es parte de la imagen de la ventana; el marco saliente (negro, de molduras u otro
                # material) se arma con cuatro cuadros planos con el material que corresponda
                if mat == MAT["glass"] and a1 - a0 > 0.4:
                    cw_c, cm_c = float(L.get("cas_w", 0.0) or 0.0), None
                    if cw_c > 0.005:
                        cm_c = MAT[SURF_MATS.get(int(L.get("cas_mat", 1)), "trim")]
                    else:
                        st_c = style_for(p, MAT["glass"], cfg)
                        if st_c and float(st_c.get("casing_w", 0.0) or 0.0) > 0.005:
                            cw_c = float(st_c["casing_w"])
                            cm_c = MAT[SURF_MATS.get(int(L.get("frm_mat", 1)), "trim")]
                    if cm_c is not None and cw_c > 0.005:
                        cw_c = max(cw_c, 0.07)
                        for (x0, x1, y0, y1) in ((a0 - cw_c, a1 + cw_c, z1, z1 + cw_c), (a0 - cw_c, a1 + cw_c, z0 - cw_c, z0),
                                                 (a0 - cw_c, a0, z0, z1), (a1, a1 + cw_c, z0, z1)):
                            if y0 < zmin - 1e-3:
                                y0 = zmin
                            if y1 - y0 < 1e-3:
                                continue
                            fv = [bm.verts.new(o + u * a + n * 0.01 + Vector((0, 0, z))) for a, z in ((x0, y0), (x1, y0), (x1, y1), (x0, y1))]
                            try:
                                bm.faces.new(fv).material_index = cm_c
                            except ValueError:
                                pass
            elif (mat == MAT["door"] or mat == MAT["gar"]) and lod == 1:     # marco de puerta/portón: un cuadro de moldura detrás (en LOD2 la puerta es un solo plano)
                fw = 0.08
                fv = [bm.verts.new(o + u * a + n * 0.02 + Vector((0, 0, z))) for a, z in
                      ((a0 - fw, max(zmin, z0)), (a1 + fw, max(zmin, z0)), (a1 + fw, z1 + fw), (a0 - fw, z1 + fw))]
                try:
                    bm.faces.new(fv).material_index = MAT["trim"]
                except ValueError:
                    pass
            elif mat != MAT["facade"] and (a1 - a0) > 0.4 and lod == 1 and (
                    tier == 2 or (tier == 5 and _hq("fr", wid, round(a0, 2), round(z0, 2)) < q)):
                fw = 0.07   # marco plano (4 cuadros) alrededor de la abertura
                for (x0, x1, y0, y1) in ((a0 - fw, a1 + fw, z1, z1 + fw), (a0 - fw, a1 + fw, z0 - fw, z0),
                                         (a0 - fw, a0, z0, z1), (a1, a1 + fw, z0, z1)):
                    fv = [bm.verts.new(o + u * a + n * 0.03 + Vector((0, 0, z))) for a, z in ((x0, y0), (x1, y0), (x1, y1), (x0, y1))]
                    try:
                        bm.faces.new(fv).material_index = MAT["trim"]
                    except ValueError:
                        pass

    if lod >= 1 and (not cut or ops_q) and float(p.get("_q", 1.0)) > 1.0 and ((lod == 2 and cs1) or (lod == 1 and p.get("lod1_tier", 0) in (5, 6))):
        qq = float(p["_q"]) - 1.0      # detalle extra cuando sobra presupuesto: alféizar y dintel de cada ventana
        ay_ = -n
        for a0, a1, z0, z1, mat in qops:
            if mat in (MAT["door"], MAT["gar"], MAT["facade"]) or a1 - a0 < 0.4:
                continue
            am, wd = (a0 + a1) / 2, a1 - a0
            if _hq("sill", wid, round(a0, 2), round(z0, 2)) < qq:
                add_obox(bm, o + u * am + n * 0.07 + Vector((0, 0, z0 - 0.05)), u, ay_, wd + 0.16, 0.14, 0.08, MAT["trim"],
                         skip=("+y", "-z") if lod == 1 else ("+y", "-z", "+x", "-x"))
            if lod == 1 and _hq("lin", wid, round(a0, 2), round(z0, 2)) < qq:
                add_obox(bm, o + u * am + n * 0.06 + Vector((0, 0, z1 + 0.06)), u, ay_, wd + 0.16, 0.12, 0.08, MAT["trim"],
                         skip=("+y", "-z"))
            if not cs1 and float(p["_q"]) > 2.0 and _hq("mul", wid, round(a0, 2), round(z0, 2)) < float(p["_q"]) - 2.0:
                zo = n * (0.055 if lod == 1 else 0.065)
                bars = [((am - 0.025, am + 0.025), (z0, z1))]
                if lod == 1:
                    bars.append(((a0, a1), ((z0 + z1) / 2 - 0.025, (z0 + z1) / 2 + 0.025)))
                for (x0, x1), (y0, y1) in bars:
                    fv = [bm.verts.new(o + u * a + zo + Vector((0, 0, z))) for a, z in ((x0, y0), (x1, y0), (x1, y1), (x0, y1))]
                    try:
                        bm.faces.new(fv).material_index = MAT["trim"]
                    except ValueError:
                        pass


def _wall_balconies_courses(bm, bals, hi_on, q_hi, p, o, u, n, wid, lod, poly, zmax, zmin, fh):
    """Balcones y cornisas de la pared según el nivel de detalle."""
    if lod <= 1:
        ay = -n
        for a_mid, zb, bd, bw, rail, bst, bww in (bals if not (lod == 1 and p.get("lod1_tier", 0) == 4) else []):  # balcones
            if bst:
                insert_balcony(bm, bst, o, u, n, a_mid, zb, bd, bw, rail, bww, p,
                               0 if (hi_on and _hq("bal2", wid, round(a_mid, 2), round(zb, 2)) < q_hi) else lod)
                continue
            base = o + u * a_mid
            add_obox(bm, base + n * (bd / 2) + Vector((0, 0, zb - 0.09)), u, ay, bw, bd, 0.18, MAT["trim"], skip=("+y",))
            add_obox(bm, base + n * (bd - 0.04) + Vector((0, 0, zb + rail / 2)), u, ay, bw, 0.08, rail, MAT["trim"])
            if lod <= 1:  # los costados de la baranda también en LOD1 (el balcón se ve completo)
                for sx in (-1, 1):
                    add_obox(bm, base + u * (sx * (bw / 2 - 0.04)) + n * (bd / 2) + Vector((0, 0, zb + rail / 2)),
                             u, ay, 0.08, bd - 0.08, rail, MAT["trim"])
        if not p["no_string_courses"] and (lod == 0 or p.get("lod1_tier", 0) in (0, 2, 5, 6)):  # cornisas entre pisos (se dibujan ingletadas al final)
            for f in range(1, int(zmax // fh) + 1):
                if f % max(1, p["string_every"]):
                    continue
                zmid = f * fh - p["string_z"]
                if not (zmin + 0.2 < zmid < zmax - 0.2):
                    continue
                prof = string_profile(p["string_style"], zmid, p["string_h"], p["string_out"])
                for s0, e0 in poly_intervals(poly, zmid):
                    if e0 - s0 > 0.5:
                        for z0, z1, dout in prof:
                            p.setdefault("_bands", []).append((o + u * s0, o + u * e0, n, z0, z1, dout))
    elif lod == 2:   # LOD2: balcones como losa simple y una moldura lisa por piso, según el presupuesto
        q = min(1.0, float(p.get("_q", 1.0)))
        ay = -n
        for a_mid, zb, bd, bw, rail, bst, bww in bals:
            if _hq("bal", wid, round(a_mid, 2), round(zb, 2)) >= q:
                continue
            st_ = dict(STYLE_DEFAULT, **bst) if bst else None
            if st_ and st_.get("bal_type") == "LOGGIA":
                continue        # la loggia es un hueco en la pared: no sale hacia afuera
            bd_ = bd or (st_["bal_depth"] if st_ else p["balcony_depth"])
            bw_ = bw or ((bww + st_["bal_extra_w"]) if st_ else bww + 0.8)
            add_obox(bm, o + u * a_mid + n * (bd_ / 2) + Vector((0, 0, zb - 0.09)), u, ay, bw_, bd_, 0.18, MAT["trim"],
                     skip=("+y", "-z", "+x", "-x"))
        if not p["no_string_courses"]:
            for f in range(1, int(zmax // fh) + 1):
                zmid = f * fh - p["string_z"]
                if f % max(1, p["string_every"]) or not (zmin + 0.2 < zmid < zmax - 0.2):
                    continue
                if _hq("band", wid, f) >= q:
                    continue
                hb = max(0.08, float(p["string_h"]) / 2)      # LOD2: la cornisa es solo textura (un cuadro plano sobre la pared)
                for s0, e0 in poly_intervals(poly, zmid):
                    if e0 - s0 > 0.5:
                        qv = [bm.verts.new(o + u * a + n * 0.02 + Vector((0, 0, z))) for a, z in
                              ((s0, zmid - hb), (e0, zmid - hb), (e0, zmid + hb), (s0, zmid + hb))]
                        try:
                            bm.faces.new(qv).material_index = MAT["trim"]
                        except ValueError:
                            pass


def _run_lod(p, lod, wid, a, zb, hi_on, q_hi):
    """Nivel de detalle con el que sale un balcón corrido (None = no sale en este LOD)."""
    if lod == 0:
        return 0
    if lod == 1:
        if p.get("lod1_tier", 0) == 4:
            return None
        return 0 if (hi_on and _hq("bal2", wid, round(a, 2), round(zb, 2)) < q_hi) else 1
    return 2 if _hq("bal", wid, round(a, 2), round(zb, 2)) < min(1.0, float(p.get("_q", 1.0))) else None


def _emit_wall_runs(bm, p, run_bals, o, u, n, amin, amax, wid, lod, hi_on, q_hi):
    """Balcón corrido en una pared recta: por piso, una franja desde el primer balcón hasta el último."""
    floors = {}
    for b in run_bals:
        floors.setdefault(round(b[1], 3), []).append(b)
    for zb, bs in sorted(floors.items()):
        st = dict(STYLE_DEFAULT, **bs[0][5])
        spans = []
        for b in bs:
            w = b[3] or (b[6] + st["bal_extra_w"])
            spans.append((b[0] - w / 2, b[0] + w / 2))
        a0, a1 = max(amin + 0.1, min(x[0] for x in spans)), min(amax - 0.1, max(x[1] for x in spans))
        if a1 - a0 < 0.6:
            continue
        lv = _run_lod(p, lod, wid, a0, zb, hi_on, q_hi)
        if lv is None:
            continue
        P0, P1 = o + u * a0, o + u * a1
        insert_balcony_run(bm, st, [(P0.x, P0.y), (P1.x, P1.y)], [n], zb, bs[0][2] or st["bal_depth"], bs[0][4], p, lv)


def _emit_chain_runs(bm, p, cfg, ch, wid, lod, hi_on, q_hi):
    """Balcón corrido en una curva: lo arma la primera faceta de cada tramo seguido de facetas con balcón y recorre todas."""
    raw = p.get("_chain_cache", {}).get((ch["id"], lod))
    rb = [b for b in (raw[1] if raw else []) if b[5] and b[5].get("bal_run")]
    if not rb:
        return
    facets = ch["facets"]
    keeps = [_chain_keep_bal(p, cfg, f["n"]) for f in facets]
    j = ch["idx"]
    if not keeps[j] or (j > 0 and keeps[j - 1]):
        return
    e = j
    while e + 1 < len(facets) and keeps[e + 1]:
        e += 1
    pts = [facets[j]["S"]] + [facets[i]["E"] for i in range(j, e + 1)]
    normals = [facets[i]["n"] for i in range(j, e + 1)]

    def shorten(q, r, d):       # acorta el extremo q hacia r en d metros
        v = Vector((r[0] - q[0], r[1] - q[1]))
        if v.length < 2 * d + 0.3:
            return q
        v.normalize()
        return (q[0] + v.x * d, q[1] + v.y * d)
    pts[0] = shorten(pts[0], pts[1], 0.1)
    pts[-1] = shorten(pts[-1], pts[-2], 0.1)
    st = dict(STYLE_DEFAULT, **rb[0][5])
    bd = rb[0][2] or st["bal_depth"]
    for zb in sorted({round(b[1], 3) for b in rb}):
        lv = _run_lod(p, lod, wid, ch["s0"], zb, hi_on, q_hi)
        if lv is None:
            continue
        insert_balcony_run(bm, st, pts, normals, zb, bd, rb[0][4], p, lv)


def _surf(i):
    """Material de las listas «Molduras / A / B / C / D» (1 a 5)."""
    return MAT[SURF_MATS.get(int(i), "trim")]


def _shop_ops(p, sh, ops, depths, amin, amax, zmin, fh):
    """Planta baja comercial: las ventanas del piso bajo se reemplazan por una vidriera (tramos de vidrio entre pilares) y el
    acceso queda dentro de uno de los tramos. Devuelve (aberturas, profundidades)."""
    keep = [(op, d) for op, d in zip(ops, depths) if not (op[4] == MAT["glass"] and op[2] < zmin + fh - 0.01)]
    length = amax - amin
    pw = min(sh["pw"], length / 3)
    zg0, zg1 = zmin + sh["base"], zmin + fh - sh["sign"] - 0.05
    if zg1 - zg0 < 0.8 or length < pw + 1.0:
        return ops, depths
    nb = max(1, int(round((length - pw) / max(1.0, sh["bay"]))))
    cs = [amin + pw / 2 + (length - pw) * i / nb for i in range(nb + 1)]
    door_i = next((i for i, (op, _d) in enumerate(keep) if op[4] == MAT["door"]), None)
    door = keep[door_i][0] if door_i is not None else None
    if door is not None and sh["ent"] > 0:
        keep[door_i] = (door, max(keep[door_i][1], sh["ent"]))      # acceso hundido
    new = []
    for i in range(nb):
        a0, a1 = cs[i] + pw / 2, cs[i + 1] - pw / 2
        if a1 - a0 < 0.5:
            continue
        pieces = [(a0, a1)]
        if door is not None and door[1] > a0 and door[0] < a1:
            pieces = []
            if door[0] - 0.1 - a0 >= 0.5:
                pieces.append((a0, door[0] - 0.1))
            if a1 - (door[1] + 0.1) >= 0.5:
                pieces.append((door[1] + 0.1, a1))
        for x0, x1 in pieces:
            new.append(((x0, x1, zg0, zg1, MAT["gls"]), sh["rec"]))
    keep += new
    return [k[0] for k in keep], [k[1] for k in keep]


def _add_awning(bm, A, B, n, depth, z_top, drop, val, mat):
    """Toldo inclinado entre A y B (puntos pegados a la pared): tapa por arriba y por abajo y el volado del frente."""
    zv = Vector((0, 0, 1))
    a0, b0 = A + zv * z_top, B + zv * z_top
    a1, b1 = A + n * depth + zv * (z_top - drop), B + n * depth + zv * (z_top - drop)
    a2, b2 = a1 - zv * val, b1 - zv * val
    for quad in ([a1, b1, b0, a0], [a0, b0, b1, a1], [a2, b2, b1, a1], [a1, b1, b2, a2]):
        try:
            bm.faces.new([bm.verts.new(v) for v in quad]).material_index = mat
        except ValueError:
            pass


def _add_shopfront(bm, o, u, n, p, sh, ops, amin, amax, zmin, fh):
    """Lo que rodea a la vidriera: pilares entre los tramos, banda de cartel y toldos."""
    ay = -n
    base_ops = sorted((op for op in ops if op[2] <= zmin + sh["base"] + 0.02 and op[4] in (MAT["gls"], MAT["door"])), key=lambda op: op[0])
    if not base_ops:
        return
    zg1 = zmin + fh - sh["sign"] - 0.05
    pm = _surf(sh["pmat"])
    prev = amin
    gaps = []
    for op in base_ops:
        gaps.append((prev, op[0]))
        prev = max(prev, op[1])
    gaps.append((prev, amax))
    for g0, g1 in gaps:
        if g1 - g0 >= 0.25:
            add_obox(bm, o + u * ((g0 + g1) / 2) + n * (sh["pd"] / 2) + Vector((0, 0, zmin + (fh - 0.05) / 2)), u, ay,
                     g1 - g0, sh["pd"], fh - 0.05, pm, skip=("+y",))
    if sh["sign"] >= 0.2:
        add_obox(bm, o + u * ((amin + amax) / 2) + n * (sh["signd"] / 2) + Vector((0, 0, zmin + fh - sh["sign"] / 2 - 0.02)), u, ay,
                 amax - amin, sh["signd"], sh["sign"], _surf(sh["smat"]), skip=("+y",))
    if int(sh["aw"]):
        am = (MAT["roof"], MAT["rail"], MAT["door"])[min(max(int(sh["awmat"]), 1), 3) - 1]
        glass = [op for op in base_ops if op[4] == MAT["gls"]]
        spans = [(min(op[0] for op in glass), max(op[1] for op in glass))] if (int(sh["aw"]) == 2 and glass) else [(op[0], op[1]) for op in glass]
        for a0, a1 in spans:
            _add_awning(bm, o + u * a0, o + u * a1, n, sh["awd"], zg1 + 0.03, 0.45, 0.22, am)


def _grid_cols(ops, skip_to):
    """Columnas de aberturas [a0, a1] (sin las que quedan por debajo de `skip_to`)."""
    cols = {}
    for op in ops:
        if op[4] not in (MAT["glass"], MAT["gls"], MAT["door"], MAT["facade"]) or op[3] <= skip_to:
            continue
        c = cols.setdefault(round(op[0], 2), [op[0], op[1]])
        c[1] = max(c[1], op[1])
    return sorted(cols.values())


def _add_grid(bm, o, u, n, p, G, ops, amin, amax, zmin, zmax, fh, shop):
    """Retícula estructural: pilastras entre las columnas de ventanas y losas (frentes de piso) en relieve; el relleno queda hundido."""
    ay = -n
    mat = _surf(G["mat"])
    mode = int(G["on"])
    length = amax - amin
    z_g = zmin + fh if shop else zmin       # sobre una vidriera no se apoyan pilastras: tiene sus propios pilares
    if mode in (1, 2) and zmax - z_g > 0.5:
        cols = _grid_cols(ops, z_g)
        pw = G["pw"]
        spots = []                           # (centro, ancho)
        if cols:
            ends = [amin] + [x for c in cols for x in c] + [amax]
            gaps = [(ends[i], ends[i + 1]) for i in range(0, len(ends), 2)]       # tramos sin aberturas: borde-col, col-col, col-borde
            for i, (g0, g1) in enumerate(gaps):
                w = min(pw, g1 - g0 - 0.15)
                if i == 0:
                    if w >= 0.15:
                        spots.append((g0 + w / 2, w))
                elif i == len(gaps) - 1:
                    if w >= 0.15:
                        spots.append((g1 - w / 2, w))
                elif w >= 0.15:
                    spots.append(((g0 + g1) / 2, w))
        else:
            nb = max(1, int(round(length / max(1.5, G["bay"]))))
            for i in range(nb + 1):
                c = min(max(amin + length * i / nb, amin + pw / 2), amax - pw / 2)
                spots.append((c, pw))
        for c, w in spots:
            add_obox(bm, o + u * c + n * (G["pd"] / 2) + Vector((0, 0, (z_g + zmax) / 2)), u, ay, w, G["pd"], zmax - z_g, mat, skip=("+y",))
    if mode in (1, 3):
        nfl = int(round((zmax - zmin) / fh))
        for k in range(1, nfl):
            if k % int(G["every"]) or (shop and k == 1):
                continue
            line = zmin + k * fh
            if any(op[2] < line - 0.01 and op[3] > line + 0.01 for op in ops):
                continue                      # una abertura alta cruza la línea de piso
            below = max([op[3] for op in ops if line - fh < op[3] <= line + 0.01] or [zmin])
            hs = min(G["sh"], line - 0.02 - below - 0.03)
            if hs < 0.12:
                continue
            add_obox(bm, o + u * ((amin + amax) / 2) + n * (G["sd"] / 2) + Vector((0, 0, line - 0.02 - hs / 2)), u, ay,
                     length, G["sd"], hs, mat, skip=("+y",))


def build_wall(bm, pts, n, p, wid, lod, cfg=None):
    """Una pared de la forma: se corta en grilla, las aberturas se hunden y se agregan balcones."""
    _L0 = look_cfg(p, cfg)
    if int(_L0.get("cw_on", 0)):     # muro cortina: reemplaza ventanas, puertas, balcones y porche de esta pared
        build_curtain(bm, pts, n, p, wid, lod, cfg, _L0)
        return
    zax = Vector((0, 0, 1))
    u = zax.cross(n).normalized()
    v0 = pts[0]
    o = Vector((v0.x, v0.y, 0.0))
    poly = [((q - v0).dot(u), q.z) for q in pts]
    zmin, zmax = min(q[1] for q in poly), max(q[1] for q in poly)
    amin, amax = min(q[0] for q in poly), max(q[0] for q in poly)
    fh = p["floor_h"]
    ops, bals, depths = shape_openings(p, poly, n, wid, lod, cfg)
    L = look_cfg(p, cfg)
    fmi = int((cfg or {}).get("f_mat", 0)) or int(L.get("wall_mat", 1))
    base_mat = (MAT["facade"], MAT["facade"], MAT["facb"], MAT["facc"], MAT["facd"])[min(max(fmi, 1), 4)]
    # planta baja con otro material (no pisa a una pared que ya eligió el suyo)
    gm = 0 if int((cfg or {}).get("f_mat", 0)) else int(L.get("ground_mat", 0))
    ground_mat = (MAT["facade"], MAT["facade"], MAT["facb"], MAT["facc"], MAT["facd"])[min(max(gm, 1), 4)] if gm else None
    if ground_mat == base_mat:
        ground_mat = None
    ground_h = fh * max(1, int(L.get("ground_n", 1)))
    # los paños de las ventanas solo van en LOD0; las crujías (columnas enteras) en todos los LOD, así el color no salta al alejarse
    pans = panel_plan(p, wid, ops, L, amin, amax, zmin, zmax, fh) if (lod == 0 or int(L["pan_mode"]) >= 6) else {}
    # LOD0 y LOD1 (niveles 0 y 1) cortan las aberturas de verdad; los niveles 2 y 3 usan cuadros pegados
    cut = lod == 0 or (lod == 1 and p.get("lod1_tier", 0) <= 1)
    cs1 = bool(p.get("_cs1"))   # malla para Cities: Skylines 1: ventanas, puertas y marcos van en la textura
    ops_q = []   # LOD1 con presupuesto de sobra: parte de las aberturas se hunden de verdad y el resto va como cuadro
    if lod == 1 and not cut and cs1 and ops:     # CS1: las loggias (huecos profundos) se hunden de verdad, como en el LOD0
        sel = [k for k in range(len(ops)) if depths[k] >= 0.6 and ops[k][4] != MAT["door"]]
        if sel:
            ops_q = [op for k, op in enumerate(ops) if k not in set(sel)]
            ops, depths = [ops[k] for k in sel], [depths[k] for k in sel]
            cut = True
    comp = lod == 1 and not cs1 and not cut and p.get("lod1_tier", 0) == 5     # LOD1 por componente: cada ventana/puerta con estilo, a la mitad
    if lod == 1 and not cut and not comp and p.get("lod1_tier", 0) == 5 and float(p.get("_q", 1.0)) > 3.0 and ops:
        qc = float(p["_q"]) - 3.0
        sel = [k for k, op in enumerate(ops) if _hq("cutop", wid, round(op[0], 2), round(op[2], 2)) < qc]
        if sel:
            ops_q = [op for k, op in enumerate(ops) if k not in set(sel)]
            ops, depths = [ops[k] for k in sel], [depths[k] for k in sel]
            cut = True
    hi_on = lod == 1 and not comp and p.get("lod1_tier", 0) == 5 and float(p.get("_q", 1.0)) > 4.0   # con presupuesto de sobra: estilos reales
    q_hi = float(p.get("_q", 1.0)) - 4.0

    def styon(k, op):
        return lod == 0 or (hi_on and _hq("sty", wid, round(op[0], 2), round(op[2], 2)) < q_hi)

    if not _wall_cut(bm, pts, n, p, cfg, ops, depths, pans, cut, o, u, v0, zmin, zmax, amin, amax, fh, base_mat, styon,
                        ground_mat, ground_h):
        return

    qops = ops_q if cut else ops
    if comp and qops:
        sty, plain = [], []
        for op in qops:
            st = style_for(p, op[4], cfg, op) if op[4] != MAT["facade"] else None
            if st and op[1] - op[0] >= 0.3 and op[3] - op[2] >= 0.3:
                sty.append((op, st))
            else:
                plain.append(op)
        qops = plain
        _wall_lod1_styled(bm, sty, o, u, n, p)
    _wall_lod_quads(bm, qops, ops_q, cut, p, cfg, L, o, u, n, wid, lod, cs1, zmin)

    if lod == 0 or hi_on:  # estilos o piezas propias (LOD0; en LOD1 solo con presupuesto de sobra)
        for k, op in enumerate(ops):
            if op[4] == MAT["facade"]:   # hueco de loggia: lo arma insert_balcony
                continue
            if not styon(k, op):
                continue
            st = style_for(p, op[4], cfg, op)
            if st:
                insert_style(bm, st, o, u, n, op, depths[k], p)
                continue
            if depths[k] <= 0:
                continue
            pool = pieces_for(p, op[4])
            if pool:
                idx = rng_for(p, "piece", wid, k).randrange(len(pool)) if p["piece_random"] else 0
                insert_piece(bm, pool[idx], o, u, n, op, p)

    if lod == 0:
        ay0 = -n
        if L["cas_w"] > 0.005:   # marcos salientes alrededor de las ventanas
            cw, cd = L["cas_w"], L["cas_d"]
            cm = MAT[SURF_MATS.get(int(L["cas_mat"]), "trim")]
            for k, (a0, a1, z0, z1, m_) in enumerate(ops):
                if m_ != MAT["glass"] or a1 - a0 < 0.4 or depths[k] < 0:
                    continue
                am = (a0 + a1) / 2
                for (cx_, cz_, sx_, sz_) in ((am, z1 + cw / 2, a1 - a0 + 2 * cw, cw), (am, z0 - cw / 2, a1 - a0 + 2 * cw, cw),
                                             (a0 - cw / 2, (z0 + z1) / 2, cw, z1 - z0), (a1 + cw / 2, (z0 + z1) / 2, cw, z1 - z0)):
                    if cz_ - sz_ / 2 < zmin - 1e-3:
                        continue
                    add_obox(bm, o + u * cx_ + n * (cd / 2) + Vector((0, 0, cz_)), u, ay0, sx_, cd, sz_, cm, skip=("+y",))
        if int(L["slt_mode"]) > 0:   # lamas verticales u horizontales sobre la parte llena de la pared
            _add_slats(bm, o, u, n, poly, ops, L, amin, amax, zmin, zmax, fh)

    G = porch_cfg(p, cfg, n, zmin, amax - amin)
    if G and lod <= 2:
        add_porch(bm, o, u, n, ops, G, lod, amin, amax, zmin, fh)

    if lod <= 1:       # planta comercial y retícula estructural (relieve sobre la pared; el LOD2 no los lleva)
        ops_all = list(ops) + (list(ops_q) if cut else [])
        SH = group_on(p, cfg, "sh", n, zmin)
        if SH and any(op[4] == MAT["gls"] for op in ops_all):
            _add_shopfront(bm, o, u, n, p, SH, ops_all, amin, amax, zmin, fh)
        GR = group_on(p, cfg, "gr", n, zmin)
        if GR:
            _add_grid(bm, o, u, n, p, GR, ops_all, amin, amax, zmin, zmax, fh, bool(SH))

    run_bals = [b for b in bals if b[5] and b[5].get("bal_run")]
    bals = [b for b in bals if not (b[5] and b[5].get("bal_run"))]
    ch_ = (cfg or {}).get("_chain")
    if lod <= 2:
        if ch_:
            _emit_chain_runs(bm, p, cfg, ch_, wid, lod, hi_on, q_hi)
        elif run_bals:
            _emit_wall_runs(bm, p, run_bals, o, u, n, amin, amax, wid, lod, hi_on, q_hi)
    _wall_balconies_courses(bm, bals, hi_on, q_hi, p, o, u, n, wid, lod, poly, zmax, zmin, fh)


STRING_STYLES = [("BAND", "Banda simple", "Una moldura lisa"),
                 ("DOUBLE", "Banda doble", "Dos filetes finos"),
                 ("STEPPED", "Escalonada", "Tres escalones que van saliendo"),
                 ("CORNICE", "Cornisa con vuelo", "Cuello fino y remate que sobresale")]


def string_profile(style, zc, h, out):
    """Perfil de la moldura entre pisos: lista de (z0, z1, saliente)."""
    lo, hi = zc - h / 2, zc + h / 2
    if style == "DOUBLE":
        t = h * 0.34
        return [(lo, lo + t, out), (hi - t, hi, out)]
    if style == "STEPPED":
        return [(lo, lo + h * 0.34, out * 0.45), (lo + h * 0.34, lo + h * 0.67, out * 0.75), (lo + h * 0.67, hi, out)]
    if style == "CORNICE":
        return [(lo, hi - h * 0.4, out * 0.45), (hi - h * 0.4, hi, out * 1.9)]
    return [(lo, hi, out)]


def roof_params(p, rcfg):
    """Parámetros efectivos de un techo: los globales o los de la cara si tiene ajustes propios."""
    rp = dict(ROOF_DEFAULT)
    rp.update(p.get("roof_cfg") or {})
    if rcfg:
        for k, v in rcfg.items():
            if k == "type":
                rp["type"] = ROOF_ID_TYPE.get(int(v), "FLAT")
            elif k != "set":
                rp[k] = v
    # techo con textura PBR: las tejas las da el mapa normal, sin sumar polígonos
    if (((p.get("matslots") or {}).get("roof") or {}).get("mode") == "PBR") and not p.get("roof_geo_tiles"):
        rp["ribs"] = 0
        rp["_tex_roof"] = True
        rp["capm"] = 1
    return rp


def roof_face_cfg(f, lays):
    lay = lays.get("cs2_r_set")
    if lay is None or not f[lay]:
        return None
    cfg = {}
    for k in ROOF_KEYS_INT + ROOF_KEYS_FLOAT:
        l = lays.get("cs2_r_" + k)
        if l is not None:
            cfg[k] = f[l]
    return cfg


def _clean_ring(P, tol=1e-4):
    """Quita vértices repetidos o alineados de un contorno 2D."""
    out = list(P)
    changed = True
    while changed and len(out) > 3:
        changed = False
        for i in range(len(out)):
            a, b, c = out[i - 1], out[i], out[(i + 1) % len(out)]
            d1, d2 = b - a, c - b
            if d1.length < tol or abs(d1.x * d2.y - d1.y * d2.x) < tol * max(d1.length, d2.length, 1e-6) * 10:
                del out[i]
                changed = True
                break
    return out


def roof_rects(pts):
    """Descompone el contorno (rectilíneo) en rectángulos. Devuelve (O, e1, e2, [(x0,x1,y0,y1)]) o None."""
    P = _clean_ring([Vector((q.x, q.y)) for q in pts])
    if len(P) < 3:
        return None
    best, e1 = 0.0, None
    for i in range(len(P)):
        d = P[(i + 1) % len(P)] - P[i]
        if d.length > best:
            best, e1 = d.length, d.normalized()
    e2 = Vector((-e1.y, e1.x))
    O = P[0]
    L = [((q - O).dot(e1), (q - O).dot(e2)) for q in P]
    for i in range(len(L)):
        dx, dy = L[(i + 1) % len(L)][0] - L[i][0], L[(i + 1) % len(L)][1] - L[i][1]
        if abs(dx) > 0.02 and abs(dy) > 0.02:
            return None  # no es rectilíneo
    ys = sorted({round(q[1], 3) for q in L})
    rects, active = [], {}
    for ya, yb in zip(ys[:-1], ys[1:]):
        if yb - ya < 0.02:
            continue
        cur = {(round(s, 3), round(e, 3)) for s, e in poly_intervals(L, (ya + yb) / 2) if e - s > 0.02}
        for key in list(active):
            if key not in cur:
                rects.append((key[0], key[1], active.pop(key), ya))
        for key in cur:
            active.setdefault(key, ya)
    for key, y0 in active.items():
        rects.append((key[0], key[1], y0, ys[-1]))
    if not rects:
        return None
    return O, e1, e2, rects


def _prism_seg(bm, A, B, w, h, mat):
    """Cumbrera / limahoya: caja a lo largo de un segmento (A, B)."""
    d = B - A
    if d.length < 1e-4:
        return
    side = d.cross(Vector((0, 0, 1)))
    if side.length < 1e-6:
        return
    side = side.normalized() * (w / 2)
    up = Vector((0, 0, h))
    lo = [bm.verts.new(A - side), bm.verts.new(B - side), bm.verts.new(B + side), bm.verts.new(A + side)]
    hi = [bm.verts.new(v.co + up) for v in lo]
    for idx in ((0, 1, 2, 3), (7, 6, 5, 4), (4, 5, 1, 0), (5, 6, 2, 1), (6, 7, 3, 2), (7, 4, 0, 3)):
        try:
            bm.faces.new([ (lo + hi)[i] for i in idx ]).material_index = MAT["trim"]
        except ValueError:
            pass


def _mkface(bm, pts, mat):
    try:
        f = bm.faces.new([bm.verts.new(q) for q in pts])
        f.material_index = mat
        return f
    except ValueError:
        return None


def _clip_strip(poly, du, u0, u1):
    """Recorta un polígono convexo [(Vector, flag)] a u0 <= du(p) <= u1; flag = borde exterior que parte de ese vértice."""
    def clip(poly, keep, sign):
        out = []
        n = len(poly)
        for i in range(n):
            (P, fl), (Q, _f2) = poly[i], poly[(i + 1) % n]
            dp, dq = sign * (du(P) - keep), sign * (du(Q) - keep)
            inp, inq = dp >= -1e-9, dq >= -1e-9
            if inp and inq:
                out.append((P, fl))
            elif inp and not inq:
                k = dp / (dp - dq)
                out.append((P, fl))
                out.append((P + (Q - P) * k, False))
            elif (not inp) and inq:
                k = dp / (dp - dq)
                out.append((P + (Q - P) * k, fl))
        return out
    poly = clip(poly, u0, 1)
    if len(poly) >= 3:
        poly = clip(poly, u1, -1)
    return poly if len(poly) >= 3 else []


def _roof_surface(bm, polys, rp, lod, H):
    """polys = [[(Vector3, flag_borde_exterior)]], CCW visto desde arriba. Arma el cuerpo del techo:
    cara superior (con tejas en relieve), cantos y cara inferior."""
    t = rp["t"] if lod == 0 else 0.0
    ribs = lod == 0 and rp["ribs"] and rp["rib_h"] > 0.004 and rp["rib_gap"] > 0.05
    for poly in polys:
        pts = [q for q, _f in poly]
        nrm = (pts[1] - pts[0]).cross(pts[2] - pts[0])
        if nrm.z < 0:
            n_ = len(poly)
            poly = [(poly[n_ - 1 - j][0], poly[(n_ - 2 - j) % n_][1]) for j in range(n_)]
        strips = [poly]
        disp = None
        if ribs:
            outer = [(poly[i][0], poly[(i + 1) % len(poly)][0]) for i in range(len(poly)) if poly[i][1]]
            horiz = [(a, b) for a, b in outer if abs(a.z - b.z) < 1e-3 and (b - a).length > 0.1]
            if not horiz:  # sin borde horizontal: usa la dirección del plano de mayor longitud horizontal
                horiz = [(poly[i][0], poly[(i + 1) % len(poly)][0]) for i in range(len(poly))
                         if abs(poly[i][0].z - poly[(i + 1) % len(poly)][0].z) < 1e-3]
            if horiz:
                a, b = max(horiz, key=lambda e: (e[1] - e[0]).length)
                cd = Vector(((b - a).x, (b - a).y)).normalized()
                du = lambda q: q.x * cd.x + q.y * cd.y
                us = [du(q) for q, _f in poly]
                gap = rp["rib_gap"]
                umin, umax = math.floor(min(us) / gap) * gap, max(us)   # fase global: las tejas coinciden entre piezas
                nst = max(1, int(math.ceil((umax - umin) / gap)))
                if nst <= 400:
                    strips = []
                    for k in range(nst):
                        sp = _clip_strip(poly, du, umin + k * gap, umin + (k + 1) * gap)
                        if sp:
                            strips.append(sp)
                    def disp(q, _du=du, _u0=0.0, _g=gap, _h=rp["rib_h"]):
                        w = ((_du(q) - _u0) / _g) % 2.0
                        return _h * (w if w <= 1.0 else 2.0 - w)
        for sp in strips:
            vv = [q + Vector((0, 0, disp(q))) if disp else q for q, _f in sp]
            _mkface(bm, vv, MAT["roof"])
            if lod == 0 and t > 0.01:
                for i, (q, fl) in enumerate(sp):
                    if fl:
                        q2 = sp[(i + 1) % len(sp)][0]
                        v0 = q + Vector((0, 0, disp(q))) if disp else q
                        v1 = q2 + Vector((0, 0, disp(q2))) if disp else q2
                        _mkface(bm, [v1, v0, q - Vector((0, 0, t)), q2 - Vector((0, 0, t))], MAT["trim"])
        if lod == 0 and t > 0.01:
            _mkface(bm, [q - Vector((0, 0, t)) for q, _f in poly[::-1]], MAT["trim"])


def _ofa(bm, pts, mat, out):
    """Cara con la normal apuntando hacia `out` (dedupe de puntos repetidos)."""
    q = []
    for v in pts:
        if not q or (v - q[-1]).length > 1e-5:
            q.append(v)
    if len(q) > 1 and (q[0] - q[-1]).length < 1e-5:
        q.pop()
    if len(q) < 3:
        return None
    nrm = Vector((0, 0, 0))
    for i in range(len(q)):
        a, b = q[i], q[(i + 1) % len(q)]
        nrm += Vector(((a.y - b.y) * (a.z + b.z), (a.z - b.z) * (a.x + b.x), (a.x - b.x) * (a.y + b.y)))
    if nrm.length < 1e-9:
        return None
    if nrm.dot(out) < 0:
        q = q[::-1]
    return _mkface(bm, q, mat)


def _dormers(bm, P, surf, rp, lod, typ, s0, s1, hb, rr, p=None):
    """Buhardillas sobre los faldones de un techo a dos aguas, cuatro aguas o una agua."""
    kind = int(rp.get("dm", 0))
    if kind <= 0 or lod >= 2 or typ not in ("GABLE", "HIP", "SHED"):
        return
    W, hw0, q = rp["dm_w"], rp["dm_h"], rp["dm_q"]
    hx = W / 2
    side = int(rp.get("dm_side", 0))
    sides = [-1] if typ == "SHED" else ([-1, 1] if side == 0 else ([-1] if side == 1 else [1]))
    lo = s0 + (hb if typ == "HIP" else 0.0) + hx + 0.5
    hi = s1 - (hb if typ == "HIP" else 0.0) - hx - 0.5
    if hi < lo:
        return
    nmax = int((hi - lo) // (W + 0.6)) + 1
    nn = max(1, min(int(rp.get("dm_n", 2)), nmax))
    centers = [(lo + hi) / 2] if nn == 1 else [lo + (hi - lo) * k / (nn - 1) for k in range(nn)]
    o0 = P(0, 0, 0)
    ax = P(1, 0, 0) - o0
    ax.z = 0
    ax.normalize()
    tdir = P(0, 1, 0) - o0
    tdir.z = 0
    tdir.normalize()
    zv = Vector((0, 0, 1))
    for sgn in sides:
        tout = tdir * sgn
        for sc in centers:
            # ajuste automático: se baja la altura y se acerca al alero hasta que la buhardilla entra en el faldón
            hw = hw0
            fit = None
            for hw_try in (hw, hw * 0.85, hw * 0.7, 1.9, 1.6, 1.35, 1.2):
                if hw_try > hw + 1e-6:
                    continue
                for qq in (q, q * 0.8, q * 0.6, q * 0.45, q * 0.3, 0.2, 0.12):
                    if qq > q + 1e-6:
                        continue
                    tf_ = sgn * hb * (1 - qq) + rr * qq
                    zf_ = surf(sc, tf_)
                    sl_ = (surf(sc, tf_ - sgn * 0.5) - zf_) / 0.5
                    run_ = abs(tf_ - rr) - 0.25
                    if sl_ >= 0.08 and run_ > 0 and hw_try / sl_ <= run_:
                        fit = (tf_, hw_try)
                        break
                if fit:
                    break
            if not fit:
                if lod == 0 and p is not None:
                    _m = "Buhardilla omitida: el faldón es muy bajo para una ventana (subí la pendiente o el ancho del techo)"
                    if _m not in p.setdefault("_warn", []):
                        p["_warn"].append(_m)
                continue
            tf, hw = fit
            zf = surf(sc, tf)
            sl = (surf(sc, tf - sgn * 0.5) - zf) / 0.5
            ztop = zf + hw
            def Q(x, d, z):          # x a lo largo de la cumbrera, d hacia atrás (hacia la cumbrera), z altura
                return P(sc + x, tf - sgn * d, z)
            def hit(z):               # d donde la altura z toca el faldón
                return max(0.0, (z - zf) / sl)
            glass_d = 0.07
            wh = min(hw - 0.45, 1.3)
            ww = max(0.4, W - 0.55)
            wz0 = zf + max(0.15, (hw - wh) / 2 - 0.1)
            wz1 = wz0 + wh
            mw, mt, mr = MAT["facade"], MAT["trim"], MAT["roof"]
            # --- frente con el hueco de la ventana
            top_poly = None
            if kind == 2:
                tp = math.tan(math.radians(32))
                Wr = hx + 0.12
                zr = ztop + hx * tp
                top_poly = [Q(-hx, 0, wz1), Q(hx, 0, wz1), Q(hx, 0, ztop), Q(0, 0, zr), Q(-hx, 0, ztop)]
            elif kind == 4:
                ra = hx * 0.75
                arc = [(hx * math.cos(math.pi * k / 10), ztop + ra * math.sin(math.pi * k / 10)) for k in range(11)]
                top_poly = [Q(-hx, 0, wz1), Q(hx, 0, wz1)] + [Q(x, 0, z) for x, z in arc[:]]
            else:
                top_poly = [Q(-hx, 0, wz1), Q(hx, 0, wz1), Q(hx, 0, ztop), Q(-hx, 0, ztop)]
            _ofa(bm, top_poly, mw, tout)
            if wz0 > zf + 0.01:
                _ofa(bm, [Q(-hx, 0, zf - 0.03), Q(hx, 0, zf - 0.03), Q(hx, 0, wz0), Q(-hx, 0, wz0)], mw, tout)
            for sx in (-1, 1):
                x0, x1 = sorted((sx * hx, sx * ww / 2))
                _ofa(bm, [Q(x0, 0, wz0), Q(x1, 0, wz0), Q(x1, 0, wz1), Q(x0, 0, wz1)], mw, tout)
            # --- vidrio y marco
            _ofa(bm, [Q(-ww / 2, glass_d, wz0), Q(ww / 2, glass_d, wz0), Q(ww / 2, glass_d, wz1), Q(-ww / 2, glass_d, wz1)], MAT["glass"], tout)
            for sx in (-1, 1):   # jambas del hueco
                _ofa(bm, [Q(sx * ww / 2, 0, wz0), Q(sx * ww / 2, glass_d, wz0), Q(sx * ww / 2, glass_d, wz1), Q(sx * ww / 2, 0, wz1)], mw, ax * -sx)
            _ofa(bm, [Q(-ww / 2, 0, wz1), Q(ww / 2, 0, wz1), Q(ww / 2, glass_d, wz1), Q(-ww / 2, glass_d, wz1)], mw, -zv)
            _ofa(bm, [Q(-ww / 2, 0, wz0), Q(ww / 2, 0, wz0), Q(ww / 2, glass_d, wz0), Q(-ww / 2, glass_d, wz0)], mw, zv)
            if lod == 0:
                fw, fd = 0.07, 0.05
                axb = ax if ax.cross(-tout).z > 0 else -ax
                for (cx_, cz_, sx_, sz_) in ((0, wz1 + fw / 2, ww + 2 * fw, fw), (0, wz0 - fw / 2, ww + 0.2 + 2 * fw, fw),
                                             (-ww / 2 - fw / 2, (wz0 + wz1) / 2, fw, wh), (ww / 2 + fw / 2, (wz0 + wz1) / 2, fw, wh)):
                    c3 = Q(cx_, -fd / 2, cz_)
                    add_obox(bm, c3, axb, -tout, sx_, fd, sz_, mt, skip=("+y",))
                if ww > 1.0:
                    add_obox(bm, Q(0, glass_d / 2, (wz0 + wz1) / 2), axb, -tout, 0.035, glass_d, wh, mt)
            if lod == 0:
                # alféizar saliente y babeta de encuentro con el faldón
                add_obox(bm, Q(0, -0.05, wz0 - fw - 0.04), axb, -tout, ww + 0.3, 0.14, 0.05, mt)
                add_obox(bm, Q(0, -0.02, zf + 0.04), axb, -tout, W + 0.16, 0.06, 0.14, mt)
                for sx in (-1, 1):   # esquineros
                    add_obox(bm, Q(sx * (hx - 0.025), -0.02, (zf + ztop) / 2), axb, -tout, 0.06, 0.05, ztop - zf, mt)
            # --- techo y mejillas, según el tipo
            if kind == 1:
                ps, ov = math.tan(math.radians(10)), 0.28
                zE = ztop + 0.08
                dh = max(0.2, (zE - zf) / max(0.05, sl - ps))
                xw = hx + 0.12
                zpl = lambda d: zE + d * ps
                _ofa(bm, [Q(-xw, -ov, zpl(-ov)), Q(xw, -ov, zpl(-ov)), Q(xw, dh, zpl(dh)), Q(-xw, dh, zpl(dh))], mr, zv)
                for sx in (-1, 1):
                    _ofa(bm, [Q(sx * hx, 0, zf - 0.03), Q(sx * hx, 0, zE), Q(sx * hx, dh, zpl(dh))], mw, ax * sx)
                if lod == 0:
                    add_obox(bm, Q(0, -ov + 0.04, zpl(-ov) - 0.05), axb, -tout, 2 * xw, 0.08, 0.1, mt)
            elif kind == 2:
                ze0 = ztop - 0.12 * tp
                de, dr = hit(ze0), hit(zr)
                for sx in (-1, 1):
                    _ofa(bm, [Q(sx * Wr, -0.25, ze0 - 0.25 * 0), Q(0, -0.25, zr + 0.0), Q(0, dr, zr), Q(sx * Wr, de, ze0)], mr, zv)
                    _ofa(bm, [Q(sx * hx, 0, zf - 0.03), Q(sx * hx, 0, ztop), Q(sx * hx, hit(ztop), ztop)], mw, ax * sx)
            elif kind == 3:
                tp = math.tan(math.radians(30))
                Wr = hx + 0.12
                ze0 = ztop - 0.12 * tp
                zr = ze0 + Wr * tp
                dm_ = -0.25 + Wr
                de, dr = hit(ze0), hit(zr)
                if dr <= dm_ + 0.05:
                    dr = dm_ + 0.05
                _ofa(bm, [Q(-Wr, -0.25, ze0), Q(Wr, -0.25, ze0), Q(0, dm_, zr)], mr, zv)
                for sx in (-1, 1):
                    _ofa(bm, [Q(sx * Wr, -0.25, ze0), Q(0, dm_, zr), Q(0, dr, zr), Q(sx * Wr, de, ze0)], mr, zv)
                    _ofa(bm, [Q(sx * hx, 0, zf - 0.03), Q(sx * hx, 0, ztop), Q(sx * hx, hit(ztop), ztop)], mw, ax * sx)
            else:   # arqueada
                for k in range(10):
                    (xa, za), (xb, zb) = arc[k], arc[k + 1]
                    pa, pb = Q(xa, 0, za), Q(xb, 0, zb)
                    qa, qb = Q(xa, hit(za), za), Q(xb, hit(zb), zb)
                    _ofa(bm, [pa, pb, qb], mr, zv + (pa + pb) / 2 * 0)
                    _ofa(bm, [pa, qb, qa], mr, zv)
                for sx in (-1, 1):
                    _ofa(bm, [Q(sx * hx, 0, zf - 0.03), Q(sx * hx, 0, ztop), Q(sx * hx, hit(ztop), ztop)], mw, ax * sx)

            if lod == 0 and kind == 2:
                # tablas de borde (alero inclinado) y cumbrera con tejas
                for sx in (-1, 1):
                    A_, B_ = Q(sx * Wr, -0.25, ze0), Q(0, -0.25, zr)
                    for dz_, mt_ in ((0.0, mt),):
                        _ofa(bm, [A_, B_, B_ - zv * 0.13, A_ - zv * 0.13], mt_, tout)
                    _ofa(bm, [A_, B_, B_ + zv * 0.0 + (Q(0, -0.1, zr) - Q(0, -0.25, zr)), A_ + (Q(0, -0.1, zr) - Q(0, -0.25, zr))], mr, zv)
                _tpp = tp
                _zr0 = zr
                _a0 = Q(0, -0.25, zr)
                _zfun = lambda x, y, _a0=_a0, _ax=ax, _tp=_tpp, _z=_zr0: _z - abs((Vector((x, y, 0.0)) - Vector((_a0.x, _a0.y, 0.0))).dot(_ax)) * _tp
                _roof_trim(bm, _a0, Q(0, dr, zr), _zfun, mt, False, 0.14, 0.04)
            elif lod == 0 and kind in (1, 3):
                if kind == 1:
                    add_obox(bm, Q(0, dh * 0.5, zpl(dh * 0.5) + 0.02), axb, -tout, 2 * xw, dh, 0.03, mr)

def _world_side(v):
    """Lado del edificio al que apunta un vector 2D: fr (-Y, el frente), bk (+Y), lf (-X), rg (+X)."""
    if abs(v.x) >= abs(v.y):
        return "rg" if v.x > 0 else "lf"
    return "bk" if v.y > 0 else "fr"


def _roof_trim(bm, A, B, zfun, mat, valley=False, w=0.2, th=0.05, tile=0.45, gap=0.012):
    """Cumbrera / limahoya detallada sobre el segmento A-B (zfun(x, y) = altura del techo).

    Cumbrera: hilera de tejas de caballete que abrazan las dos aguas, con una pequeña separación entre piezas.
    Limahoya: canaleta de chapa que sigue las dos aguas."""
    d = Vector((B.x - A.x, B.y - A.y, 0.0))
    L = d.length
    if L < 0.05:
        return
    dn = d / L
    pp = Vector((-dn.y, dn.x, 0.0))
    up = Vector((0.0, 0.0, 1.0))
    n = 1 if valley else max(1, int(round(L / tile)))

    def section(u):
        M = A + (B - A) * u
        zl = zfun(M.x - pp.x * w, M.y - pp.y * w)
        zr = zfun(M.x + pp.x * w, M.y + pp.y * w)
        zc = zfun(M.x, M.y) if valley else M.z
        if not valley:
            zl, zr = min(zl, zc), min(zr, zc)
        Lb = Vector((M.x - pp.x * w, M.y - pp.y * w, zl))
        Rb = Vector((M.x + pp.x * w, M.y + pp.y * w, zr))
        Cb = Vector((M.x, M.y, zc))
        return Lb, Cb, Rb

    def ends(sec, out):
        Lb, Cb, Rb = sec
        _ofa(bm, [Lb, Lb + up * th, Cb + up * th, Rb + up * th, Rb, Cb], mat, out)

    for k in range(n):
        u0 = k / n + (gap / L if (k > 0 and not valley) else 0.0)
        u1 = (k + 1) / n - (gap / L if (k < n - 1 and not valley) else 0.0)
        a0, a1 = section(u0), section(u1)
        (L0, C0, R0), (L1, C1, R1) = a0, a1
        t_ = up * th
        outL = (-pp * 0.5 + up) if not valley else up
        outR = (pp * 0.5 + up) if not valley else up
        _ofa(bm, [L0 + t_, C0 + t_, C1 + t_, L1 + t_], mat, outL)
        _ofa(bm, [C0 + t_, R0 + t_, R1 + t_, C1 + t_], mat, outR)
        _ofa(bm, [L0, L0 + t_, L1 + t_, L1], mat, -pp)
        _ofa(bm, [R0, R0 + t_, R1 + t_, R1], mat, pp)
        if k == 0:
            ends(a0, -dn)
        if k == n - 1:
            ends(a1, dn)


def _roof_neighbors(rects, eps=0.03):
    """Para cada rectángulo del techo, qué rectángulos lo tocan por cada lado (+x, -x, +y, -y)."""
    n = len(rects)
    nb = [{"+x": [], "-x": [], "+y": [], "-y": []} for _ in range(n)]
    for i in range(n):
        for j in range(n):
            if i == j:
                continue
            ax0, ax1, ay0, ay1 = rects[i]
            bx0, bx1, by0, by1 = rects[j]
            if abs(ax1 - bx0) < eps and min(ay1, by1) - max(ay0, by0) > 0.05:
                nb[i]["+x"].append(j)
            if abs(ax0 - bx1) < eps and min(ay1, by1) - max(ay0, by0) > 0.05:
                nb[i]["-x"].append(j)
            if abs(ay1 - by0) < eps and min(ax1, bx1) - max(ax0, bx0) > 0.05:
                nb[i]["+y"].append(j)
            if abs(ay0 - by1) < eps and min(ax1, bx1) - max(ax0, bx0) > 0.05:
                nb[i]["-y"].append(j)
    return nb


def _roof_chimneys(bm, P, surf, a, b, s0, s1, hb, rp, lod):
    """Chimeneas sobre el rectángulo de techo más grande."""
    cw, cd_ = max(0.3, rp["chw"]), max(0.3, rp["chd"])
    poss = [rp["chpos"], 1.0 - rp["chpos"]][:int(rp["chn"])]
    for k, pos in enumerate(poss):
        s = s0 + (s1 - s0) * pos
        tt = max(-0.92, min(0.92, rp["chside"] * (1 if k == 0 else -1))) * hb
        cors = [(s + sx * cw / 2, tt + ty * cd_ / 2) for sx in (-1, 1) for ty in (-1, 1)]
        zs = [surf(q[0], q[1]) for q in cors]
        zb, zt_ = min(zs) - 0.35, max(zs) + rp["chh"]
        c3 = P(s, tt, (zb + zt_) / 2)
        ax3, ay3 = Vector((a.x, a.y, 0)), Vector((b.x, b.y, 0))
        add_obox(bm, c3, ax3, ay3, cw, cd_, zt_ - zb, MAT["facade"], skip=("-z",))
        if rp["chcap"] and lod == 0:
            add_obox(bm, P(s, tt, zt_ + 0.04), ax3, ay3, cw + 0.16, cd_ + 0.16, 0.08, MAT["trim"])
            if rp["chcap"] == 2:
                for dx in (-1, 1):
                    add_obox(bm, P(s + dx * cw * 0.22, tt, zt_ + 0.08 + 0.17), ax3, ay3, 0.2, 0.2, 0.34, MAT["trim"])


def _roof_valleys(bm, valleys, sfs, rp):
    """Limahoyas entre rectángulos: la altura es la mayor de las dos aguas."""
    for A_, B_, i_, j_ in valleys:
        fi, fj = sfs[i_], sfs[j_]
        if fi is None or fj is None:
            continue
        zf = (lambda x, y, _fi=fi, _fj=fj: max(_fi(x, y), _fj(x, y)))
        A2, B2 = Vector((A_.x, A_.y, zf(A_.x, A_.y))), Vector((B_.x, B_.y, zf(B_.x, B_.y)))
        _roof_trim(bm, A2, B2, zf, MAT["roof"] if rp.get("_tex_roof") else MAT["trim"], True, 0.26, 0.14)


def roof_build(bm, pts, rp, lod, p):
    """Techo procedural sobre una cara plana superior. Devuelve False si no se pudo (se usa techo plano)."""
    dec = roof_rects(pts)
    if dec is None:
        return False
    O, e1, e2, rects = dec
    H = max(q.z for q in pts)
    typ = rp["type"]
    tn = math.tan(math.radians(max(3.0, min(80.0, rp["pitch"]))))
    n = len(rects)
    nb = _roof_neighbors(rects)
    area = [(r[1] - r[0]) * (r[3] - r[2]) for r in rects]
    along_list = []
    for (x0, x1, y0, y1) in rects:
        al = (x1 - x0) >= (y1 - y0)
        if typ in ("GABLE", "SHED", "BUTTERFLY") and rp["ridge"] == 1:
            al = not al
        along_list.append(al)
    sfs = [None] * n          # altura del techo de cada rectángulo en coordenadas del mundo
    valleys = []               # (A, B, i, j): limahoyas por armar cuando todos los rectángulos están hechos
    for i, (x0, x1, y0, y1) in enumerate(rects):
        wx, wy = x1 - x0, y1 - y0
        along_x = wx >= wy      # el caballete va por el lado largo (en cuatro aguas siempre)
        if typ in ("GABLE", "SHED", "BUTTERFLY") and rp["ridge"] == 1:
            along_x = not along_x
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        if along_x:
            a, b = e1, e2
            ha, hb = wx / 2, wy / 2
            sm, sp_, tm, tp = "-x", "+x", "-y", "+y"
            span_other = lambda j: rects[j][3] - rects[j][2]
            depth = lambda j: rects[j][1] - rects[j][0]
        else:
            a, b = e2, -e1
            ha, hb = wy / 2, wx / 2
            sm, sp_, tm, tp = "-y", "+y", "+x", "-x"
            span_other = lambda j: rects[j][1] - rects[j][0]
            depth = lambda j: rects[j][3] - rects[j][2]
        ctr = O + e1 * cx + e2 * cy
        s0, s1 = -ha, ha
        free_m = free_p = True
        ext_m = ext_p = False          # este techo se mete dentro del vecino (más ancho): no necesita hastial en ese extremo
        for side, key in ((sm, "m"), (sp_, "p")):
            for j in nb[i][side]:
                bigger = span_other(j) > 2 * hb + 0.05 or (abs(span_other(j) - 2 * hb) <= 0.05 and j < i)
                if key == "m":
                    free_m = False
                    if bigger:
                        s0 = min(s0, -ha - depth(j) / 2 - 0.1)
                        ext_m = True
                else:
                    free_p = False
                    if bigger:
                        s1 = max(s1, ha + depth(j) / 2 + 0.1)
                        ext_p = True
        oe = rp["oe"]
        lin = typ in ("GABLE", "SHED", "BUTTERFLY")
        ov = rp["ov"] if lin else oe
        flip = typ == "SHED" and rp["flip"]
        nbm, nbp = nb[i][tm], nb[i][tp]
        if flip:
            b = -b
            nbm, nbp = nbp, nbm

        def pick(base, outward):      # alero propio de ese lado del edificio, o el general
            if not lin:
                return base
            v = rp.get("sd_" + _world_side(outward), -1.0)
            return base if v < 0 else v
        oe_m, oe_p = pick(oe, -b), pick(oe, b)

        def explicit(outward):      # alero que el usuario fijó a mano para ese lado (None = el general)
            if not lin:
                return None
            v_ = rp.get("sd_" + _world_side(outward), -1.0)
            return v_ if v_ >= 0 else None
        ex_m, ex_p = explicit(-b), explicit(b)
        # junto a otro techo el alero es 0 por defecto, pero si el usuario fijó un alero en ese lado se respeta (así se puede ampliar)
        ov_m = pick(ov, -a) if free_m else (explicit(-a) or 0.0)
        ov_p = pick(ov, a) if free_p else (explicit(a) or 0.0)

        def cover(nlist):
            """Tramos (en s) del lado largo que ya están tapados por otro rectángulo: ahí no hay alero."""
            out_ = []
            for j in nlist:
                bx0, bx1, by0, by1 = rects[j]
                if along_x:
                    lo_, hi_ = max(x0, bx0) - cx, min(x1, bx1) - cx
                else:
                    lo_, hi_ = max(y0, by0) - cy, min(y1, by1) - cy
                if along_list[j] != along_x:      # techo perpendicular: su alero también tapa un poco
                    lo_, hi_ = lo_ - oe, hi_ + oe
                out_.append((lo_, hi_))
            return sorted(out_)
        cov_m = cover(nbm) if (lin and oe_m > 1e-4 and ex_m is None) else ([] if lin else None)
        cov_p = cover(nbp) if (lin and oe_p > 1e-4 and ex_p is None) else ([] if lin else None)
        if not lin:
            cov_m = cov_p = []
        if lin and nbm and not cov_m and ex_m is None:
            oe_m = 0.0
        if lin and nbp and not cov_p and ex_p is None:
            oe_p = 0.0
        def P(s, t, z):
            return Vector((ctr.x + a.x * s + b.x * t, ctr.y + a.y * s + b.y * t, z))
        rt = rp["t"] * 1.3
        # encuentros con un techo más grande y perpendicular en los extremos del caballete (limahoyas en diagonal)
        jn = {-1: None, 1: None}
        if lin and typ == "GABLE":
            for side_key, sg in ((sm, -1), (sp_, 1)):
                for j in nb[i][side_key]:
                    bigger_ = span_other(j) > 2 * hb + 0.05 or (abs(span_other(j) - 2 * hb) <= 0.05 and j < i)
                    if not bigger_ or along_list[j] == along_x:
                        continue
                    bx0, bx1, by0, by1 = rects[j]
                    lo_t, hi_t = (max(y0, by0), min(y1, by1)) if along_x else (max(x0, bx0), min(x1, bx1))
                    if hi_t - lo_t >= 2 * hb - 0.1:
                        jn[sg] = j
        polys, caps, vcaps = [], [], []

        def mk(zt_, sa, sb, ta, tb, fta, ftb):
            """Cuadrilátero de una pendiente entre las líneas t=ta y t=tb, de s=sa a s=sb (flag = borde exterior)."""
            return [(P(sa, ta, zt_(ta)), fta), (P(sb, ta, zt_(ta)), True), (P(sb, tb, zt_(tb)), ftb), (P(sa, tb, zt_(tb)), True)]

        def free_parts(S0_, S1_, cov):
            cur, out_ = S0_, []
            for lo_, hi_ in cov:
                if lo_ > cur + 0.02:
                    out_.append((cur, min(lo_, S1_)))
                cur = max(cur, hi_)
            if cur < S1_ - 0.02:
                out_.append((cur, S1_))
            return [(u, v) for u, v in out_ if v - u > 0.02]

        def slope(zt_, S0_, S1_, tw, tout, tr, cov):
            """Una pendiente de la pared (t=tw) hasta la cumbrera (t=tr), con alero (hasta t=tout) solo donde no hay otro techo."""
            if abs(tout - tw) < 1e-4 or not cov:
                return [mk(zt_, S0_, S1_, tout, tr, True, False)]
            res = [mk(zt_, S0_, S1_, tw, tr, False, False)]
            for fa, fb in free_parts(S0_, S1_, cov):
                res.append(mk(zt_, fa, fb, tout, tw, True, False))
            return res
        wall_hi = wall_hi_b = None
        rr_d = 0.0
        if typ == "GABLE":
            rr = max(-0.8, min(0.8, rp.get("peak", 0.0))) * hb      # cumbrera descentrada (0 = al medio)
            rr_d = rr
            hr = max(hb + rr, hb - rr) * tn                          # el lado largo manda la inclinación
            zt = lambda t: H + (hr * (t + hb) / (rr + hb) if t <= rr else hr * (hb - t) / (hb - rr))
            S0, S1, T0, T1 = s0 - ov_m, s1 + ov_p, -(hb + oe_m), hb + oe_p
            polys += slope(zt, S0, S1, -hb, T0, rr, cov_m)
            polys += slope(zt, S0, S1, hb, T1, rr, cov_p)
            cs0 = -(ha + hb) if (jn[-1] is not None and abs(rr) < 1e-6) else S0
            cs1 = (ha + hb) if (jn[1] is not None and abs(rr) < 1e-6) else S1
            caps.append((P(cs0, rr, zt(rr)), P(cs1, rr, zt(rr))))
            ends = [(s0, free_m or not ext_m), (s1, free_p or not ext_p)]   # junto a un techo más angosto y más bajo, el hastial queda a la vista
            fill = lambda s: [(-hb, H), (hb, H), (rr, zt(rr) - rt)]
            surf = lambda s, t: zt(t)
        elif typ == "BUTTERFLY":
            zt = lambda t: H + abs(t) * tn          # valle al medio a la altura de la pared; los bordes suben
            S0, S1, T0, T1 = s0 - ov_m, s1 + ov_p, -(hb + oe_m), hb + oe_p
            polys += slope(zt, S0, S1, -hb, T0, 0.0, cov_m)
            polys += slope(zt, S0, S1, hb, T1, 0.0, cov_p)
            vcaps.append((P(S0, 0, zt(0)), P(S1, 0, zt(0))))
            ends = [(s0, free_m or not ext_m), (s1, free_p or not ext_p)]   # junto a un techo más angosto y más bajo, el hastial queda a la vista
            fill = lambda s: [(-hb, H), (hb, H), (hb, zt(hb) - rt), (0, H), (-hb, zt(-hb) - rt)]
            surf = lambda s, t: zt(t)
            if not nb[i][tm]:
                wall_hi = [P(s0, -hb, H), P(s1, -hb, H), P(s1, -hb, zt(-hb) - rt), P(s0, -hb, zt(-hb) - rt)]
            if not nb[i][tp]:
                wall_hi_b = [P(s0, hb, H), P(s1, hb, H), P(s1, hb, zt(hb) - rt), P(s0, hb, zt(hb) - rt)]
        elif typ == "SHED":
            zt = lambda t: H + (t + hb) * tn
            rr_d = hb
            S0, S1, T0, T1 = s0 - ov_m, s1 + ov_p, -(hb + oe_m), hb + oe_p
            if not cov_m and not cov_p:
                polys.append(mk(zt, S0, S1, T0, T1, True, True))
            else:
                polys.append(mk(zt, S0, S1, -hb, hb, False, False))
                for fa, fb in free_parts(S0, S1, cov_m):
                    if abs(T0 + hb) > 1e-4:
                        polys.append(mk(zt, fa, fb, T0, -hb, True, False))
                for fa, fb in free_parts(S0, S1, cov_p):
                    if abs(T1 - hb) > 1e-4:
                        polys.append(mk(zt, fa, fb, hb, T1, False, True))
            ends = [(s0, free_m or not ext_m), (s1, free_p or not ext_p)]   # junto a un techo más angosto y más bajo, el hastial queda a la vista
            fill = lambda s: [(-hb, H), (hb, H), (hb, zt(hb) - rt)]
            surf = lambda s, t: zt(t)
            if not nb[i][tm if flip else tp]:   # muro del lado alto: sube hasta el techo (si no, queda un hueco)
                wall_hi = [P(s0, hb, H), P(s1, hb, H), P(s1, hb, zt(hb) - rt), P(s0, hb, zt(hb) - rt)]
        else:  # HIP / MANSARD
            tn1 = tn
            if typ == "MANSARD":
                ins = min(rp["brk"], hb * 0.8)
                tn2 = math.tan(math.radians(max(3.0, min(80.0, rp["pitch2"]))))
                zi = H + ins * tn1
                A, B, C, D = P(s0 - oe, -(hb + oe), H - oe * tn1), P(s1 + oe, -(hb + oe), H - oe * tn1), \
                    P(s1 + oe, hb + oe, H - oe * tn1), P(s0 - oe, hb + oe, H - oe * tn1)
                A2, B2, C2, D2 = P(s0 + ins, -(hb - ins), zi), P(s1 - ins, -(hb - ins), zi), \
                    P(s1 - ins, hb - ins, zi), P(s0 + ins, hb - ins, zi)
                polys += [[(A, True), (B, False), (B2, False), (A2, False)], [(B, True), (C, False), (C2, False), (B2, False)],
                          [(C, True), (D, False), (D2, False), (C2, False)], [(D, True), (A, False), (A2, False), (D2, False)]]
                hb2 = hb - ins
                r0, r1 = s0 + ins + hb2, s1 - ins - hb2
                zr = zi + hb2 * tn2
                if r1 - r0 < 1e-3:
                    R = P((r0 + r1) / 2, 0, zr)
                    polys += [[(A2, False), (B2, False), (R, False)], [(B2, False), (C2, False), (R, False)],
                              [(C2, False), (D2, False), (R, False)], [(D2, False), (A2, False), (R, False)]]
                    caps += [(A2, R), (B2, R), (C2, R), (D2, R)]
                else:
                    R0, R1 = P(r0, 0, zr), P(r1, 0, zr)
                    polys += [[(A2, False), (B2, False), (R1, False), (R0, False)], [(B2, False), (C2, False), (R1, False)],
                              [(C2, False), (D2, False), (R0, False), (R1, False)], [(D2, False), (A2, False), (R0, False)]]
                    caps += [(R0, R1), (A2, R0), (D2, R0), (B2, R1), (C2, R1)]
                caps += [(A, A2), (B, B2), (C, C2), (D, D2)]
                def surf(s, t, _s0=s0, _s1=s1):
                    d = min(s - _s0, _s1 - s, hb - abs(t))
                    return H + tn1 * min(d, ins) + tn2 * max(0.0, d - ins)
            else:
                zz = H - oe * tn
                A, B, C, D = P(s0 - oe, -(hb + oe), zz), P(s1 + oe, -(hb + oe), zz), P(s1 + oe, hb + oe, zz), P(s0 - oe, hb + oe, zz)
                r0, r1 = s0 + hb, s1 - hb
                zr = H + hb * tn
                if r1 - r0 < 1e-3:
                    R = P((r0 + r1) / 2, 0, zr)
                    polys += [[(A, True), (B, False), (R, False)], [(B, True), (C, False), (R, False)],
                              [(C, True), (D, False), (R, False)], [(D, True), (A, False), (R, False)]]
                    caps += [(A, R), (B, R), (C, R), (D, R)]
                else:
                    R0, R1 = P(r0, 0, zr), P(r1, 0, zr)
                    polys += [[(A, True), (B, False), (R1, False), (R0, False)], [(B, True), (C, False), (R1, False)],
                              [(C, True), (D, False), (R0, False), (R1, False)], [(D, True), (A, False), (R0, False)]]
                    caps += [(R0, R1), (A, R0), (D, R0), (B, R1), (C, R1)]
                def surf(s, t, _s0=s0, _s1=s1):
                    return H + tn * min(s - _s0, _s1 - s, hb - abs(t))
            ends = []
            fill = None
        _roof_surface(bm, polys, rp, lod, H)
        for wh, sgn in ((wall_hi, 1 if typ != "BUTTERFLY" else -1), (wall_hi_b, 1)):
            if wh:
                nn = (wh[1] - wh[0]).cross(wh[2] - wh[0])
                if nn.dot(Vector((b.x, b.y, 0))) * sgn < 0:
                    wh = wh[::-1]
                _mkface(bm, wh, MAT["facade"])
        # hastiales (paño de fachada hasta el techo) en los extremos libres
        if fill is not None:
            for s, free in ends:
                if free:
                    fp = [P(s, tt, zz) for tt, zz in fill(s)]
                    outd = a * (1 if s == s1 else -1)
                    nn = (fp[1] - fp[0]).cross(fp[2] - fp[0])
                    if Vector((nn.x, nn.y, 0)).dot(Vector((outd.x, outd.y, 0))) < 0:
                        fp = fp[::-1]
                    _mkface(bm, fp, MAT["facade"])
        if rp.get("dm") and typ in ("GABLE", "HIP", "SHED"):
            _dormers(bm, P, surf, rp, lod, typ, s0, s1, hb, rr_d, p)
        # altura del techo de este rectángulo en coordenadas del mundo (para cumbreras y limahoyas)
        def sw(x, y, _c=ctr, _a=a, _b=b, _sf=surf):
            dx_, dy_ = x - _c.x, y - _c.y
            return _sf(dx_ * _a.x + dy_ * _a.y, dx_ * _b.x + dy_ * _b.y)
        sfs[i] = sw
        if lod == 0 and rp["cap"]:
            cmat = MAT["roof"] if int(rp.get("capm", 0)) == 1 else MAT["trim"]
            for A_, B_ in caps:
                _roof_trim(bm, A_, B_, sw, cmat, False, 0.2, 0.055, 1.6 if rp.get("_tex_roof") else 0.45)
            for A_, B_ in vcaps:
                _roof_trim(bm, A_, B_, sw, MAT["roof"] if rp.get("_tex_roof") else MAT["trim"], True, 0.26, 0.14)
            if typ == "GABLE" and abs(rr_d) < 1e-6:
                # limahoyas en diagonal donde este techo se mete contra uno mayor y perpendicular
                for sg in (-1, 1):
                    if jn[sg] is None:
                        continue
                    for sgt in (-1, 1):
                        oe_w = oe_p if sgt > 0 else oe_m
                        oev = max(0.0, min(oe_w, pick(oe, a * (-sg))))
                        V0 = P(sg * (ha - oev), sgt * (hb + oev), H - oev * tn)
                        V1 = P(sg * (ha + hb), 0.0, H + hb * tn)
                        valleys.append((V0, V1, i, jn[sg]))
            # limahoyas entre techos paralelos pegados por el lado largo (L, T, U)
            if typ in ("GABLE", "BUTTERFLY"):
                for key_, tsg in ((tm, -1), (tp, 1)):
                    for j in nb[i][key_]:
                        if j < i or along_list[j] != along_x:
                            continue
                        bx0, bx1, by0, by1 = rects[j]
                        lo_, hi_ = (max(x0, bx0) - cx, min(x1, bx1) - cx) if along_x else (max(y0, by0) - cy, min(y1, by1) - cy)
                        if hi_ - lo_ > 0.3:
                            valleys.append((P(lo_, tsg * hb, H), P(hi_, tsg * hb, H), i, j))
        # chimeneas (en el rectángulo más grande)
        if lod <= 1 and rp["chn"] > 0 and area[i] >= max(area) - 1e-6:
            _roof_chimneys(bm, P, surf, a, b, s0, s1, hb, rp, lod)
    if lod == 0 and rp["cap"]:
        _roof_valleys(bm, valleys, sfs, rp)
    return True


def _drain(gen):
    """Corre un generador de pasos hasta el final y devuelve su resultado (modo sin interfaz)."""
    try:
        while True:
            next(gen)
    except StopIteration as e:
        return e.value


def build_bmesh_shape(p, lod, shape):
    return _drain(build_bmesh_shape_iter(p, lod, shape))


def build_bmesh_shape_iter(p, lod, shape):
    """Arma la malla de un LOD. Cede (avance 0-1, texto) entre pasos para poder mostrar progreso y cancelar."""
    bm = bmesh.new()
    try:
        yield from _build_shape_steps(bm, p, lod, shape)
    except BaseException:
        bm.free()
        raise
    return bm


def _build_shape_steps(bm, p, lod, shape):
    bm.loops.layers.uv.new("UVMap")
    bm.faces.layers.int.new("is_piece")
    trot = bm.faces.layers.int.new("trot")     # giro de la textura de la pared, en cuartos de vuelta
    p["_dissolve"] = lod >= 1
    p["_lod"] = lod
    p["_bands"] = []
    nw = max(1, len(shape["walls"]))
    for wid, (pts, n, cfg) in enumerate(shape["walls"]):
        q = int((cfg or {}).get("f_rot", 0)) % 4
        before = set(bm.faces) if q else None
        build_wall(bm, pts, n, p, wid, lod, cfg)
        if q:
            for f_ in bm.faces:
                if f_ not in before:
                    f_[trot] = q
        yield 0.85 * (wid + 1) / (nw + 1), f"LOD{lod}: pared {wid + 1} de {nw}"
    if p["_bands"]:
        add_band_group(bm, p["_bands"], 0.05, p["string_out"], MAT["trim"])
    p["_bands"] = []
    for pts, rn, rcfg in shape["roofs"]:
        rp = roof_params(p, rcfg)
        done = False
        if rp["type"] != "FLAT" and rn.z > 0.98:
            try:
                done = roof_build(bm, pts, rp, (lod - 1) if (lod >= 1 and float(p.get("_q", 1.0)) > 2.5) else lod, p)
            except Exception as e:  # una planta rara no debe romper todo el edificio
                print("CS2: techo:", e)
                done = False
            if not done:
                p.setdefault("_warn", []).append("Un techo no se pudo armar (planta no rectangular): quedó plano")
        if not done:
            try:
                bm.faces.new([bm.verts.new(c) for c in pts]).material_index = MAT["roof"]
            except ValueError:
                pass
    if lod <= 1:
        pgroups, cgroups = {}, {}
        for a, b, nw, rc in shape["parapet_edges"]:
            rp = roof_params(p, rc)
            if rp["type"] != "FLAT":
                continue
            if not rp.get("nopar"):
                ph, pt = (rp["par_h"] or p["parapet_h"]), (rp["par_t"] or 0.25)
                pgroups.setdefault(pt, []).append((a, b, nw, a.z, a.z + ph))
            if not rp.get("nocor"):
                ch, co = (rp["cor_h"] or 0.3), max(0.0, rp["cor_o"])
                cgroups.setdefault((ch, co, rp["par_t"] or 0.25), []).append((a, b, nw, a.z - ch, a.z))
        for pt, lst in pgroups.items():
            add_band_group(bm, lst, pt, 0.0, MAT["trim"])
        if lod == 0:
            for (ch, co, pt), lst in cgroups.items():
                add_band_group(bm, lst, pt, co, MAT["trim"])
    yield 0.88, f"LOD{lod}: techos"
    finish_bmesh(bm, p)
    yield 1.0, f"LOD{lod}: texturas (UV)"


def shape_footprint(kind, w, d):
    """Planta (antihoraria) centrada en el origen."""
    if kind == "L":
        pts = [(0, 0), (w, 0), (w, d * 0.45), (w * 0.45, d * 0.45), (w * 0.45, d), (0, d)]
    elif kind == "T":
        pts = [(0, d * 0.55), (w * 0.3, d * 0.55), (w * 0.3, 0), (w * 0.7, 0), (w * 0.7, d * 0.55),
               (w, d * 0.55), (w, d), (0, d)]
    elif kind == "U":
        pts = [(0, 0), (w * 0.35, 0), (w * 0.35, d * 0.6), (w * 0.65, d * 0.6), (w * 0.65, 0),
               (w, 0), (w, d), (0, d)]
    else:
        pts = [(0, 0), (w, 0), (w, d), (0, d)]
    return [(x - w / 2, y - d / 2) for x, y in pts]


def split_floors(bm, fh, zbase=None):
    """Corta las paredes de la forma en cada piso (un plano horizontal cada `fh` metros)."""
    if not len(bm.verts) or fh <= 0:
        return
    zs = [v.co.z for v in bm.verts]
    z0, z1 = min(zs), max(zs)
    zb = z0 if zbase is None else zbase
    k = int(math.floor((z0 - zb) / fh)) + 1
    while zb + k * fh < z1 - 1e-4:
        z = zb + k * fh
        if z > z0 + 1e-4:
            geom = list(bm.verts) + list(bm.edges) + list(bm.faces)
            bmesh.ops.bisect_plane(bm, geom=geom, dist=1e-5, plane_co=(0, 0, z), plane_no=(0, 0, 1))
        k += 1


def create_shape_object(context, kind, w, d, h, fh=0.0, pts=None):
    """Crea una malla base extruida y dividida por pisos (para editar con Tab). Siempre un objeto nuevo.
    `pts`: planta propia (lista de (x, y) antihoraria); si no, la de `kind`."""
    bm = bmesh.new()
    face = bm.faces.new([bm.verts.new((x, y, 0.0)) for x, y in (pts or shape_footprint(kind, w, d))])
    ret = bmesh.ops.extrude_face_region(bm, geom=[face])
    top = [g for g in ret["geom"] if isinstance(g, bmesh.types.BMVert)]
    bmesh.ops.translate(bm, vec=(0.0, 0.0, h), verts=top)
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    if fh > 0:
        split_floors(bm, fh)
    me = bpy.data.meshes.new("CS2_Forma")
    bm.to_mesh(me)
    bm.free()
    obj = bpy.data.objects.new("CS2_Forma", me)
    context.scene.collection.objects.link(obj)
    return obj


# ----------------------------------------------------------------------------
# Materiales y escena
# ----------------------------------------------------------------------------
MAT_COLORS = {  # colores lineales
    "glass": (0.16, 0.28, 0.40), "gls": (0.16, 0.28, 0.40), "trim": (0.81, 0.77, 0.68),
    "roof": (0.15, 0.115, 0.097), "door": (0.066, 0.039, 0.021),
    "facb": (0.30, 0.073, 0.046),   # ladrillo
    "facc": (0.034, 0.034, 0.038),  # gris oscuro
    "facd": (0.45, 0.133, 0.027),   # madera / corten
    "gar": (0.55, 0.55, 0.52),      # portón de garaje
    "rail": (0.81, 0.77, 0.68),     # barandas, rejas y metal
}


def get_materials(p):
    """Reutiliza los materiales si ya existen, así no se pierden las texturas que les pongas."""
    mats = []
    for name in MAT_ORDER:
        mname = f"{p['building_name']}_{name}"
        m = bpy.data.materials.get(mname)
        if m is None:
            m = bpy.data.materials.new(mname)
            if m.node_tree is None:
                m.use_nodes = True
            bsdf = m.node_tree.nodes.get("Principled BSDF")
            if bsdf:
                bsdf.inputs["Roughness"].default_value = 0.15 if name in ("glass", "gls") else 0.8
                if name in MAT_COLORS:
                    bsdf.inputs["Base Color"].default_value = (*MAT_COLORS[name], 1.0)
        if name == "facade" and m.node_tree:
            bsdf = m.node_tree.nodes.get("Principled BSDF")
            if bsdf:
                bsdf.inputs["Base Color"].default_value = (*p["facade_color"], 1.0)
        if name in ("trim", "roof", "door", "facb", "facc", "facd", "gar", "rail") and p.get("colors") and name in p["colors"]:
            col = tuple(p["colors"][name])
            m.diffuse_color = (*col, 1.0)
            m["cs2_viewport_color"] = True
            bsdf = m.node_tree.nodes.get("Principled BSDF") if m.node_tree else None
            if bsdf and not bsdf.inputs["Base Color"].is_linked:
                bsdf.inputs["Base Color"].default_value = (*col, 1.0)
        # Color del visor (modo Sólido): sin esto todos los materiales se ven grises
        # y las ventanas no se distinguen de la pared.
        if name == "facade":
            m.diffuse_color = (*p["facade_color"], 1.0)
        elif name in ("facb", "facc", "facd") and p.get("colors"):
            m.diffuse_color = (*p["colors"][name], 1.0)
        elif name in MAT_COLORS and m.get("cs2_viewport_color") is None:
            m.diffuse_color = (*MAT_COLORS[name], 1.0)
            m["cs2_viewport_color"] = True
        mats.append(m)
    return mats


def footprint(s):
    if s.custom_size:
        return s.width, s.depth
    return s.cells_x * CELL - 1.0, s.cells_z * CELL - 1.0


def _live_style_override(p):
    """El estilo del editor, si 'Probar en el edificio' está activo, reemplaza al general."""
    spp = getattr(bpy.context.scene, "cs2_style", None)
    if spp is not None and spp.apply_live:
        live = {**STYLE_DEFAULT, **style_from_editor(spp)}
        p[{"door": "door_style", "balcony": "balcony_style"}.get(live["kind"], "window_style")] = live
    return p


def props_to_params(s, seed=None):
    p = _live_style_override(_props_to_params(s, seed))
    fh = float(p.get("_tiley_h", p.get("floor_h", 3.0)))
    for sl in ("facade", "facb", "facc", "facd"):
        d = (p.get("matslots") or {}).get(sl)
        if d is not None:
            if slot_quarter_turn(d):      # el piso se repite sobre u: se estira el ancho de la repetición, no el alto
                d["_tx"] = slot_tile_y(d, fh, axis=0)
            else:
                d["_ty"] = slot_tile_y(d, fh)
    return p


def _props_to_params(s, seed=None):
    w, d = footprint(s)
    sill = s.win_sill
    seed = s.seed if seed is None else seed
    var = s.variation
    rnd = random.Random(f"{seed}|base")
    r = [rnd.random() for _ in range(5)]  # siempre las mismas 5 tiradas, para que sea estable
    j = s.floors_jitter
    floors = max(1, s.floors + (min(int(r[0] * (2 * j + 1)), 2 * j) - j))
    color = tuple(min(1.0, c * (1 + (r[1] - 0.5) * 0.5 * var)) for c in s.facade_color)
    corner = (1 if r[2] < 0.5 else -1, 1 if r[3] < 0.5 else -1) if var > 0 else (1, 1)
    return {
        "building_name": s.building_name or "Residencial01",
        "width": w, "depth": d,
        "floors": floors, "floor_h": s.floor_height,
        "win_w": s.win_w,
        "win_h": max(0.4, min(s.win_h, s.floor_height - sill - 0.3)),
        "sill": sill, "min_gap": 1.0, "win_depth": s.win_depth,
        "door_h": min(2.2, s.floor_height - 0.4),
        "balcony_depth": s.bal_depth_g, "bal_w_g": s.bal_w_g, "bal_rail_g": s.bal_rail_g,
        "door_w_g": s.door_w_g, "door_h_g": s.door_h_g, "parapet_h": 0.8,
        "balconies": s.balconies, "balcony_prob": s.balcony_prob,
        "balcony_walls": s.balcony_walls,
        "roof": s.roof, "roof_height": s.roof_height, "roof_cfg": roof_values(s),
        "no_string_courses": not s.string_courses,
        "string_h": s.string_h, "string_out": s.string_out, "string_style": s.string_style,
        "string_every": s.string_every, "string_z": s.string_z, "no_roof_box": not s.roof_box,
        "facade_color": color, "uv_tile": s.uv_tile,
        "colors": {"trim": tuple(s.trim_color), "roof": tuple(s.roof_color), "door": tuple(s.door_color),
                   "facb": tuple(s.mat_b_color), "facc": tuple(s.mat_c_color), "facd": tuple(s.mat_d_color),
                   "gar": tuple(s.gar_color), "rail": tuple(s.rail_color)},
        "_tiley_h": s.floor_height,
        "matslots": {sl: {k: (tuple(v) if k in ("tint", "emis_col") else (bpy.path.abspath(v) if (v and k.startswith("img_")) else v))
                          for k, v in ((k, getattr(s, f"ms_{sl}_{k}")) for k in SLOT_DEFAULTS)} for sl in PBR_SLOTS},
        "roof_geo_tiles": bool(s.roof_geo_tiles), "pbr_preview": bool(s.pbr_preview), "pbr_prev_size": int(s.pbr_prev_size), "bake_ao": bool(s.bake_ao),
        "bake_ao_str": float(s.bake_ao_str), "bake_dist": float(s.bake_dist), "bake_samples": int(s.bake_samples),
        "bake_margin": int(s.bake_margin), "bake_share": bool(s.bake_share), "bake_share_loose": float(s.bake_share_loose), "bake_share_area": float(s.bake_share_area), "bake_roof_wt": float(s.bake_roof_wt), "bake_align": bool(s.bake_align), "bake_align_x": bool(s.bake_align_x), "uv_smart": bool(s.uv_smart), "uv_group": bool(s.uv_group), "uv_grid": bool(s.uv_grid), "uv_same_density": bool(s.uv_same_density), "uv_c_wall": float(s.uv_c_wall), "uv_c_trim": float(s.uv_c_trim), "uv_c_roof": float(s.uv_c_roof), "uv_c_door": float(s.uv_c_door), "uv_c_gar": float(s.uv_c_gar), "uv_c_rail": float(s.uv_c_rail), "uv_pins": s.uv_pins, "tex_share_lods": bool(s.tex_share_lods),
        "tex_share_asset": s.tex_share_asset.strip(),
        "groups": {key: {**{k: int(getattr(s, f"{key}_{k}_g")) for k in g["ints"]},
                         **{k: float(getattr(s, f"{key}_{k}_g")) for k in g["floats"]}} for key, g in NEW_GROUPS.items()},
        "porch": {"on": bool(s.por_on_g), "walls": int(s.por_walls_g),
                  **{k: int(getattr(s, "por_" + k + "_g")) for k in PORCH_INTS if k != "on"},
                  **{k: float(getattr(s, "por_" + k + "_g")) for k in PORCH_FLOATS}},
        "look": {"cw_on": int(s.cw_on_g), "cw_mw": s.cw_mw_g, "cw_mh": s.cw_mh_g, "cw_pt": s.cw_pt_g, "cw_pd": s.cw_pd_g,
                 "cw_band": s.cw_band_g, "cw_edge": s.cw_edge_g, "cw_bmat": int(s.cw_bmat_g), "cw_mmat": int(s.cw_mmat_g),
                 "cw_fmat": int(s.cw_fmat_g), "cw_finn": int(s.cw_finn_g), "cw_find": s.cw_find_g, "cw_fint": s.cw_fint_g,
                 **{"cw_" + k: float(getattr(s, "cw_" + k + "_g")) for k in _CW_NEW_F},
                 **{"cw_" + k: int(getattr(s, "cw_" + k + "_g")) for k in _CW_NEW_I},
                 "pan_mode": int(s.pan_mode_g), "pan_mat": int(s.pan_mat_g), "pan_pad": s.pan_pad_g,
                 "pan_prob": s.pan_prob_g, "cas_w": s.cas_w_g, "cas_d": s.cas_d_g, "cas_mat": int(s.cas_mat_g),
                 "slt_mode": int(s.slt_mode_g), "slt_gap": s.slt_gap_g, "slt_w": s.slt_w_g, "slt_d": s.slt_d_g,
                 "slt_mat": int(s.slt_mat_g), "wall_mat": int(s.wall_mat_g), "frm_mat": int(s.frm_mat_g),
                 "ground_mat": int(s.ground_mat_g), "ground_n": int(s.ground_n_g)},
        "win_rooms": s.win_rooms, "tex_size": int(s.tex_size),
        "seed": seed, "variation": var, "roof_box_corner": corner,
        "piece_random": s.piece_random,
        "balcony_style": None if s.balcony_style in ("NONE", "") else get_style(s.balcony_style),
        "window_style": None if s.window_style in ("NONE", "") else get_style(s.window_style),
        "door_style": None if s.door_style in ("NONE", "") else get_style(s.door_style),
        "window_pieces": load_pieces(s.window_collection),
        "door_pieces": load_pieces(s.door_collection),
        "_extra_mats": [],
    }


def get_collection(context, name):
    coll = bpy.data.collections.get(name)
    if coll is None:
        coll = bpy.data.collections.new(name)
        context.scene.collection.children.link(coll)
    return coll


def make_building(context, p, coll, origin=(0.0, 0.0, 0.0), separate=True, hide_lods=False, shape=None):
    """Crea (o reemplaza) los 3 LODs de un edificio dentro de la colección dada.

    Con `shape` se genera desde la malla base; sin él, desde la caja paramétrica.
    """
    return _drain(make_building_iter(context, p, coll, origin, separate, hide_lods, shape))


def make_building_iter(context, p, coll, origin=(0.0, 0.0, 0.0), separate=True, hide_lods=False, shape=None,
                       span=(0.0, 1.0), only=None, clean=False, fit_lods=True, main_only=False):
    """Igual que make_building, pero cede (avance, texto) entre pasos. Los LODs se arman primero y recién al
    final reemplazan a los anteriores: si se cancela a mitad de camino, el edificio previo queda intacto."""
    mats = get_materials(p)
    keep = {}
    if only is not None:      # regenerar solo algunos LOD: los demás quedan como están
        for lod_ in range(3):
            if lod_ not in only:
                ob_ = bpy.data.objects.get(f"{p['building_name']}_LOD{lod_}")
                if ob_ is None:
                    raise RuntimeError("Primero creá el edificio (falta el LOD0)")
                keep[lod_] = ob_
        p["_atlas"], p["_ext"] = bake_context(p)   # LOD1 y LOD2 heredan el atlas del LOD0
    else:
        p.pop("_ext", None)
        p.pop("_atlas", None)
    qs = {1: 5.0, 2: 5.0} if fit_lods else {1: 1.0, 2: 1.0}   # se empieza con todo el detalle y fit() lo baja hasta el presupuesto
    gap = p["width"] * 1.4
    center = shape["center"] if shape else Vector((0.0, 0.0, 0.0))
    p["lod1_tier"] = 5
    pending = []   # mallas nuevas aún sin objeto: se borran si la generación se cancela
    lo, hi = span
    ranges = {0: (lo, lo + 0.62 * (hi - lo)), 1: (lo + 0.62 * (hi - lo), lo + 0.9 * (hi - lo)), 2: (lo + 0.9 * (hi - lo), hi)}

    def make_mesh(lod):
        name = f"{p['building_name']}_LOD{lod}"
        p["_extra_mats"] = []
        p["_q"] = qs.get(lod, 1.0)
        r0, r1 = ranges[lod]
        if shape:
            bm = yield from _scaled(build_bmesh_shape_iter(p, lod, shape), r0, r1)
        else:
            bm = build_bmesh(p, lod)
        try:
            mesh = bpy.data.meshes.new("_cs2_tmp_" + name)
            pending.append(mesh)
            bm.to_mesh(mesh)
        finally:
            bm.free()
        for m_ in mats + p["_extra_mats"]:
            mesh.materials.append(m_)
        if clean:   # se limpia al crearla: así el ajuste de triángulos de cada LOD mide lo que de verdad queda
            try:
                clean_hidden_faces(mesh)
            except Exception as e:
                print("CS2: limpiar malla:", e)
        return mesh

    def link_lod(lod, mesh):
        name = f"{p['building_name']}_LOD{lod}"
        old = bpy.data.objects.get(name)
        if old is not None:
            old_mesh = old.data
            bpy.data.objects.remove(old, do_unlink=True)
            if old_mesh.users == 0:
                bpy.data.meshes.remove(old_mesh)
        mesh.name = name
        obj = bpy.data.objects.new(name, mesh)
        obj.hide_select = bool(_LOCK.get("on")) and shape is not None
        coll.objects.link(obj)
        obj.location = (origin[0] + center.x + (lod * gap if separate else 0.0),
                        origin[1] + center.y, origin[2] + center.z)
        if hide_lods and lod > 0:
            try:
                obj.hide_set(True)
            except RuntimeError:
                pass
        return obj

    def refit(lod, tier=None):
        if tier is not None:
            p["lod1_tier"] = tier
        old_m = meshes[lod]
        meshes[lod] = yield from make_mesh(lod)
        if old_m in pending:
            pending.remove(old_m)
        bpy.data.meshes.remove(old_m)
        return mesh_tris(meshes[lod])

    def fit(lod, lo, hi_, target, tiers=(), qmax=5.0):
        """Ajusta la riqueza q (0 = lo básico, 1 = todos los marcos/molduras, 2 = también alféizares y dinteles)
        para que los triángulos del LOD caigan entre lo y hi_. Si ni con q=0 entra, prueba niveles más simples."""
        qs[lod] = qmax
        qa, ta = qmax, mesh_tris(meshes[lod])
        if ta <= hi_:
            return ta                      # ya entra con todo el detalle
        qs[lod] = 0.0
        qb = 0.0
        tb = yield from refit(lod)
        if tb > hi_:
            for tier in tiers:             # más simple todavía
                tb = yield from refit(lod, tier)
                if tb <= hi_:
                    break
            return tb
        best = (tb, 0.0)
        for _ in range(9):                 # regula falsi con el intervalo [qb (entra), qa (se pasa)]
            q = qb + (target - tb) * (qa - qb) / max(1, ta - tb)
            q = min(max(q, qb + 0.04 * (qa - qb)), qa - 0.04 * (qa - qb))
            qs[lod] = q
            t = yield from refit(lod)
            if lo <= t <= hi_:
                return t
            if t > hi_:
                qa, ta = q, t
            else:
                qb, tb = q, t
                best = (t, q)
        qs[lod] = best[1]                  # no cayó en el rango: se queda con lo mejor que entraba
        return (yield from refit(lod))

    try:
        meshes = []
        for lod in (range(1) if main_only else range(3)):
            if lod in keep:
                meshes.append(keep[lod].data)
            else:
                meshes.append((yield from make_mesh(lod)))
        t0 = mesh_tris(meshes[0])
        if shape and t0 and fit_lods and not main_only:
            if 1 not in keep:   # LOD1: cerca de la mitad de los triángulos del LOD0 (siempre por debajo del 50%)
                yield from fit(1, 0.46 * t0, 0.497 * t0, 0.48 * t0, tiers=(3, 4))
            if 2 not in keep:   # LOD2: cerca de 500 triángulos (sin pasarse)
                yield from fit(2, 455, LOD2_MAX_TRIS - 3, 482)
                if mesh_tris(meshes[2]) > LOD2_MAX_TRIS - 3:      # ni con lo mínimo entra: se unen caras casi coplanares
                    shave_tris(meshes[2], LOD2_MAX_TRIS - 3)
        yield hi, "Colocando el edificio"
        objs = [keep[lod] if lod in keep else link_lod(lod, m) for lod, m in enumerate(meshes)]
        pending.clear()
        if main_only:             # los LOD1 y LOD2 viejos ya no coinciden con la malla nueva: se generan de nuevo cuando hagan falta
            for lod_ in (1, 2):
                old_ = bpy.data.objects.get(f"{p['building_name']}_LOD{lod_}")
                if old_ is not None:
                    old_me = old_.data
                    bpy.data.objects.remove(old_, do_unlink=True)
                    if old_me is not None and old_me.users == 0:
                        bpy.data.meshes.remove(old_me)
    except BaseException:   # cancelado o con error: no deja mallas sueltas
        for m in pending:
            try:
                if m.users == 0:
                    bpy.data.meshes.remove(m)
            except (ReferenceError, RuntimeError):
                pass
        raise
    for ob in objs:   # el diseño del atlas viaja con el edificio (lo usan las texturas y la exportación)
        ob["cs2_ext"] = float(p.get("_ext", ATLAS_SCALE))
        ob["cs2_atlas"] = json.dumps({k: list(v) for k, v in (p.get("_atlas") or ATLAS).items()})
    try:
        update_preview(p)   # con «Vista rápida» activa, nace ya con las texturas del atlas
    except Exception as e:
        print("CS2: vista previa de texturas:", e)
    return objs


def _scaled(gen, r0, r1):
    """Reescala el avance (0-1) de un generador de pasos al tramo r0-r1 del avance total."""
    try:
        while True:
            f, msg = next(gen)
            yield r0 + f * (r1 - r0), msg
    except StopIteration as e:
        return e.value


LOD1_MAX_RATIO = 0.5   # el LOD1 debe tener menos del 50% de triángulos del LOD0 (wiki de CS2)
LOD2_MAX_TRIS = 500    # el LOD2 de edificios: máximo 500 triángulos


def mesh_tris(obj):
    """Triángulos de la malla principal (el vidrio va en mallas aparte: _Win y _Gls)."""
    me = obj if isinstance(obj, bpy.types.Mesh) else obj.data
    return sum(len(poly.vertices) - 2 for poly in me.polygons if poly.material_index not in SUB_MATS)


def shave_tris(me, limit):
    """Último recurso para entrar en el límite de triángulos: une caras casi coplanares del mismo material con ángulos
    cada vez mayores (las UV del atlas se conservan). Devuelve los triángulos que quedan."""
    t = mesh_tris(me)
    if t <= limit:
        return t
    bm = bmesh.new()
    try:
        bm.from_mesh(me)
        for ang in (2.0, 5.0, 10.0, 20.0, 35.0):
            bmesh.ops.dissolve_limit(bm, angle_limit=math.radians(ang), verts=bm.verts, edges=bm.edges,
                                     use_dissolve_boundaries=False, delimit={"MATERIAL"})
            bm.normal_update()
            t = sum(len(f.verts) - 2 for f in bm.faces if f.material_index not in SUB_MATS)
            if t <= limit:
                break
        if t > limit:        # sigue pasándose: se sacan las piezas más chicas de baranda y moldura (apenas se notan de lejos)
            cand = sorted((f for f in bm.faces if f.material_index in (MAT["rail"], MAT["trim"])), key=lambda f: f.calc_area())
            drop, cut = [], t
            for f in cand:
                if cut <= limit:
                    break
                drop.append(f)
                cut -= len(f.verts) - 2
            if drop:
                bmesh.ops.delete(bm, geom=drop, context="FACES")
        bm.to_mesh(me)
        me.update()
    finally:
        bm.free()
    return mesh_tris(me)


def lod_report(objs):
    """[(texto, ok)] con los triángulos de cada LOD y si cumplen los límites del juego."""
    t = [mesh_tris(o) for o in objs]
    out = [(f"Detalle alto (LOD0): {t[0]} triángulos", True)]
    r = t[1] / t[0] if t[0] else 0.0
    out.append((f"Detalle medio (LOD1): {t[1]} ({r * 100:.0f}% del alto, máx. 50%)", r < LOD1_MAX_RATIO))
    out.append((f"Detalle bajo (LOD2): {t[2]} (máx. {LOD2_MAX_TRIS})", t[2] <= LOD2_MAX_TRIS))
    return out


def sub_tris(obj, mat_index):
    return sum(len(poly.vertices) - 2 for poly in obj.data.polygons if poly.material_index == mat_index)


def load_shape_for(s):
    """Lee la forma base elegida; RuntimeError con mensaje claro si falta."""
    obj = s.shape_object
    if obj is None or obj.type != "MESH":  # sin forma elegida: se crea una caja con las medidas del lote
        w, d = footprint(s)
        obj = create_shape_object(bpy.context, "BOX", w, d, s.floors * s.floor_height, s.floor_height)
        live = s.live_update
        s.live_update = False
        s.shape_object = obj
        s.live_update = live
    return read_shape(obj, s.shape_recalc, s.floor_height if s.snap_floors else None, s.curve_deg)


def regenerate(context, live=False, only=None):
    """Reconstruye los 3 LODs del edificio principal con los parámetros actuales.
    live=True (al mover un valor del panel) no limpia caras ocultas, para que responda rápido."""
    return _drain(regenerate_iter(context, live, only))


def regenerate_iter(context, live=False, only=None):
    """Igual que regenerate, pero cede (avance, texto) para mostrar progreso y permitir cancelar."""
    s = context.scene.cs2_building
    _o0 = bpy.data.objects.get(f"{s.building_name or 'Residencial01'}_LOD0")
    if only is None and _o0 is not None and _o0.get("cs2_manual"):
        raise RuntimeError("Este edificio viene de tu malla: usá «Generar desde mi malla» (o cambiá el nombre para crear uno nuevo)")
    if s.mode != "shape":  # el modo "caja" se unificó: ahora todo es una forma
        live = s.live_update
        s.live_update = False
        s.mode = "shape"
        s.live_update = live
    p = props_to_params(s)
    shape = None
    if s.mode == "shape":
        shape = load_shape_for(s)
        p["width"], p["depth"] = shape["size"].x, shape["size"].y
        obj = s.shape_object
        if not obj.get("cs2_wire_done"):  # la forma base pasa a verse en alambre (una sola vez)
            obj.display_type = "WIRE"
            obj.hide_render = True  # es solo una guía: no debe taparse con las paredes
            obj["cs2_wire_done"] = True
    coll = get_collection(context, f"CS2_{p['building_name']}")
    cleaning = (not live) and s.clean_on_generate
    objs = yield from make_building_iter(context, p, coll, separate=s.separate_lods, shape=shape,
                                         span=(0.0, 1.0), only=only, clean=cleaning, fit_lods=not live, main_only=only is None)
    if s.show_grid and not live:
        try:
            make_grid(context)
        except Exception:
            _log_exc("regenerate_iter")
    if only is None:
        _PENDING[0] = False
    return objs


def safe_regenerate(operator, context):
    try:
        return regenerate(context)
    except RuntimeError as e:
        operator.report({"ERROR"}, str(e))
        return None


def export_fbx(path, objs, **extra):
    for o in context_objects():
        o.select_set(False)
    for o in objs:
        o.select_set(True)
    bpy.context.view_layer.objects.active = objs[0]
    kw = dict(global_scale=1.0)
    kw.update(extra)
    bpy.ops.export_scene.fbx(
        filepath=path, use_selection=True, object_types={"MESH"},
        apply_unit_scale=True, **kw,
        # Rotación y escala horneadas en los vértices: el nodo sale sin transformaciones
        # (el importador del juego las ignora y el edificio quedaba chico y girado).
        bake_space_transform=True, apply_scale_options="FBX_SCALE_NONE",
        axis_forward="-Z", axis_up="Y", mesh_smooth_type="FACE", add_leaf_bones=False,
    )


def context_objects():
    return list(bpy.context.view_layer.objects)


def lot_cells(w, d):
    """Celdas de 8 m que ocupa una planta (redondeo hacia arriba, tolerancia 5 cm)."""
    return max(1, math.ceil(w / CELL - 0.006)), max(1, math.ceil(d / CELL - 0.006))


# Tipos de zona (wiki oficial, Asset Pipeline: Buildings). El importador elige el interior de las ventanas
# por una palabra clave en el nombre del asset: Industrial, Office, ResidentialHigh, ResidentialLow,
# CommercialHigh o CommercialLow (un nombre propio usa la variante Office).
ZONE_TYPES = {
    "RESLOW": ("Residencial · baja densidad", "ResidentialLow"),
    "RESROW": ("Residencial · media densidad (hileras)", "ResidentialLow"),
    "RESMED": ("Residencial · media densidad", "ResidentialHigh"),
    "RESHIGH": ("Residencial · alta densidad", "ResidentialHigh"),
    "RESMIX": ("Residencial · mixto (local abajo)", "ResidentialHigh"),
    "RESRENT": ("Residencial · alquiler bajo", "ResidentialLow"),
    "COMLOW": ("Comercial · baja densidad", "CommercialLow"),
    "COMHIGH": ("Comercial · alta densidad", "CommercialHigh"),
    "OFFLOW": ("Oficinas · baja densidad", "Office"),
    "OFFHIGH": ("Oficinas · alta densidad", "Office"),
    "IND": ("Industrial · manufactura", "Industrial"),
    "WARE": ("Industrial · depósito", "Industrial"),
    "OTHER": ("Otro (servicio o emblemático)", ""),
}
# Triángulos de la malla principal en el juego base: (promedio, máximo recomendado). No son límites duros.
ZONE_BUDGET = {"RESLOW": (8000, 15000), "RESROW": (6000, 14000), "RESMED": (35000, 75000), "RESHIGH": (50000, 95000),
               "RESMIX": (15000, 28000), "RESRENT": (15000, 25000), "COMHIGH": (14000, 30000),
               "OFFHIGH": (40000, 78000), "IND": (3000, 6000)}
ASSET_CATEGORY_TEXT = {"ZONED": "Zonificado", "SERVICE": "Servicio", "SIGNATURE": "Emblemático"}


def level_for(s):
    """Solo los edificios zonificados tienen niveles L1-L5; servicio y emblemáticos van sin nivel."""
    return s.level if s.asset_category == "ZONED" else "NONE"


def name_keyword(s):
    """Palabra clave del nombre que fija el interior de las ventanas, o ''."""
    return ZONE_TYPES.get(s.zone_type, ("", ""))[1]


def budget_note(s, tris):
    """(texto, ok) con la referencia de triángulos del juego para la zona elegida, o None."""
    b = ZONE_BUDGET.get(s.zone_type)
    if not b or s.asset_category == "SIGNATURE":
        return None
    avg, mx = b
    ok = tris <= mx
    return (f"Referencia del juego: promedio {avg:,} · máximo recomendado {mx:,}".replace(",", "."), ok)


def lot_warning(w, d):
    """Texto de advertencia si el lote no entra en 1x2..6x6 celdas, o None."""
    cx, cz = lot_cells(w, d)
    try:
        zoned = bpy.context.scene.cs2_building.asset_category == "ZONED"
    except Exception:
        zoned = True
    if not zoned:
        return None
    if cx > 6 or cz > 6:
        return f"Lote {cx}x{cz} celdas: el máximo de un edificio zonificado es 6x6"
    if cx * cz < 2:
        return f"Lote {cx}x{cz} celdas: el mínimo zonificado es 1x2"
    return None


def asset_export_name(theme, level, name, w, d):
    """Nombre del importador: <Tema>_<Nombre>_L<nivel>_<AxB>. El nombre no lleva guiones bajos."""
    cx, cz = lot_cells(w, d)
    base = "".join(ch for ch in name if ch.isalnum()) or "Residencial01"
    parts = ([] if theme == "NONE" else [theme]) + [base] + ([] if str(level) == "NONE" else [f"L{level}"]) + [f"{cx}x{cz}"]
    return "_".join(parts)


def _srgb(c):
    """Color lineal -> sRGB (0-1), que es como se guarda en un PNG."""
    return tuple(12.92 * x if x <= 0.0031308 else 1.055 * (x ** (1 / 2.4)) - 0.055 for x in c[:3])


ATLAS_GLOSS = {"facade": 0.15, "trim": 0.25, "roof": 0.12, "door": 0.40, "gls": 0.90,
               "facb": 0.12, "facc": 0.30, "facd": 0.20, "gar": 0.35, "rail": 0.45}


PBR_SLOTS = ("facade", "facb", "facc", "facd", "trim", "roof", "door", "gar", "rail")
PBR_SLOT_LABEL = {"facade": "Pared (A)", "facb": "Material B", "facc": "Material C", "facd": "Material D",
                  "trim": "Molduras y marcos", "roof": "Techo", "door": "Puertas",
                  "gar": "Portones de garaje", "rail": "Barandas, rejas y metal"}
PBR_MAP_FIELDS = (("img_color", "Difuso (color)"), ("img_normal", "Normal"), ("img_rough", "Rugosidad / especular"),
                  ("img_metal", "Metálico"), ("img_ao", "Oclusión (AO)"), ("img_emis", "Emisivo"))
SLOT_DEFAULTS = dict(mode="FLAT", set="", img_color="", img_normal="", img_rough="", img_metal="", img_ao="", img_emis="",
                     tint=(1.0, 1.0, 1.0), tile=2.0, nstr=1.0, flipg=False, gmode="ROUGH", metal=0.0, gloss=0.15,
                     gmult=1.0, coat=0.0, aostr=1.0, emis_col=(0.0, 0.0, 0.0), emis_str=1.0, cmask="0", snow=True, rot=0.0)
_TILE_CACHE = {}


def _read_tile(path, w, h):
    """Imagen del disco llevada a w x h (se reduce a la mitad por pasos para no generar ruido). RGBA 0-1 sin convertir."""
    import numpy as np
    try:
        stamp = os.path.getmtime(path)
    except OSError:
        return None
    key = (path, stamp, w, h)
    hit = _TILE_CACHE.get(key)
    if hit is not None:
        return hit
    img = bpy.data.images.load(path, check_existing=False)
    try:
        try:
            img.colorspace_settings.name = "Non-Color"   # valores tal cual (el PNG final los guarda así)
        except Exception:
            _log_exc("_read_tile")
        cw, ch = img.size
        if cw < 1 or ch < 1:
            return None
        while cw // 2 >= w and ch // 2 >= h:
            cw, ch = cw // 2, ch // 2
            img.scale(cw, ch)
        if (cw, ch) != (w, h):
            img.scale(w, h)
        arr = np.empty(w * h * img.channels, np.float32)
        img.pixels.foreach_get(arr)
        arr = arr.reshape(h, w, img.channels)
        if img.channels < 4:
            out = np.ones((h, w, 4), np.float32)
            out[..., :img.channels] = arr
            arr = out
    finally:
        bpy.data.images.remove(img)
    if len(_TILE_CACHE) > 40:
        _TILE_CACHE.clear()
    _TILE_CACHE[key] = arr
    return arr


_PERIOD_CACHE = {}


def slot_tile_y(sl, floor_h, axis=1):
    """Alto (m) con que se repite la textura de un sector en vertical: el pedido, estirado un poco (pocos %) para que
    el alto de piso sea un número entero de períodos del dibujo (hiladas). Así todos los pisos caen en la misma fase del
    ladrillo y los paneles iguales de pisos distintos pueden compartir isla sin que se corran.
    Con axis=0 hace lo mismo en horizontal (textura girada 90°: la altura del piso cae sobre el eje u del sector)."""
    tl = max(0.05, float(sl.get("tile", SLOT_DEFAULTS["tile"])))
    path = sl.get("img_color", "")
    if sl.get("mode") != "PBR" or not path or floor_h <= 0.2:
        return tl
    fp = _pattern_period(path)[0 if axis == 0 else 1]
    per = tl * fp
    n = max(1, round(floor_h / per))
    ty = floor_h / (n * fp)
    return ty if abs(ty / tl - 1.0) <= 0.15 else tl


def slot_quarter_turn(sl):
    """True si la textura del material va girada un cuarto de vuelta (90° o 270°)."""
    try:
        r = float(sl.get("rot", 0.0)) % 180.0
    except (TypeError, ValueError):
        return False
    return abs(r - 90.0) < 1e-3


def _pattern_period(path):
    """Período real del patrón de una textura que se repite (ladrillo, teja): (fx, fy), fracción de la imagen en x e y
    tras la cual el dibujo vuelve a caer igual. Sin patrón claro devuelve (1, 1) (toda la imagen)."""
    import numpy as np
    try:
        key = (path, os.path.getmtime(path))
    except OSError:
        return (1.0, 1.0)
    if key in _PERIOD_CACHE:
        return _PERIOD_CACHE[key]
    res = (1.0, 1.0)
    try:
        n = 128
        a = _read_tile(path, n, n)
        if a is not None:
            g = a[..., :3].mean(axis=2).astype(np.float64)
            g -= g.mean()
            f = np.fft.rfft2(g)
            ac = np.fft.irfft2(f * np.conj(f), s=g.shape)
            ac /= max(ac[0, 0], 1e-12)

            def first_peak(v):
                m = len(v) // 2
                best = float(v[2:m + 1].max()) if m > 2 else 0.0
                if best < 0.35:
                    return 1.0
                for k in range(2, m + 1):
                    if v[k] >= 0.85 * best and v[k] >= v[k - 1] and v[k] >= v[min(k + 1, len(v) - 1)]:
                        return k / float(len(v))
                return 1.0
            fx0, fy0 = first_peak(ac[0, :]), first_peak(ac[:, 0])
            # una textura que se repite tiene un número entero de períodos: se ajusta a 1/k
            res = (1.0 / max(1, round(1.0 / fx0)), 1.0 / max(1, round(1.0 / fy0)))
    except Exception:
        res = (1.0, 1.0)
    _PERIOD_CACHE[key] = res
    return res


def _tile_to(arr, W, H):
    import numpy as np
    ph, pw = arr.shape[:2]
    return np.tile(arr, (-(-H // ph), -(-W // pw), 1))[:H, :W]


def slot_maps(name, sl, flat_rgb, W, H, ppm):
    """Mapas de un sector del atlas: dict con base (H,W,3), normal (H,W,3), mask (H,W,4), emis (H,W,3)."""
    import numpy as np
    gl = SLOT_DEFAULTS["gloss"]
    sl = {**SLOT_DEFAULTS, "gloss": ATLAS_GLOSS.get(name, gl), **(sl or {})}
    base = np.empty((H, W, 3), np.float32)
    base[:] = flat_rgb
    normal = np.empty((H, W, 3), np.float32)
    normal[:] = (0.5, 0.5, 1.0)
    mask = np.zeros((H, W, 4), np.float32)
    mask[..., 0] = sl["metal"]
    mask[..., 1] = sl["coat"]
    mask[..., 3] = sl["gloss"]
    emis = np.zeros((H, W, 3), np.float32)
    emis[:] = np.clip(np.array(sl["emis_col"][:3], np.float32) * sl["emis_str"], 0.0, 1.0)
    if sl["mode"] == "PBR":
        pp = int(max(16, min(1024, round(float(sl.get("_tx") or sl["tile"]) * ppm))))
        ppy = int(max(16, min(1024, round(float(sl.get("_ty") or sl["tile"]) * ppm))))

        def tile(path):
            if not path or not os.path.isfile(path):
                return None
            a = _read_tile(path, pp, ppy)
            return None if a is None else _tile_to(a, W, H)
        c = tile(sl["img_color"])
        if c is not None:
            base[:] = c[..., :3] * np.array(sl["tint"][:3], np.float32)
        else:
            base[:] = np.clip(np.array(flat_rgb, np.float32) * np.array(sl["tint"][:3], np.float32), 0, 1)
        ao = tile(sl["img_ao"])
        if ao is not None and sl["aostr"] > 0:
            base *= (1.0 - sl["aostr"] + sl["aostr"] * ao[..., :1])
        n = tile(sl["img_normal"])
        if n is not None:
            v = n[..., :3] * 2.0 - 1.0
            if sl["flipg"]:
                v[..., 1] = -v[..., 1]
            v[..., :2] *= sl["nstr"]
            ln = np.maximum(np.linalg.norm(v, axis=2, keepdims=True), 1e-6)
            normal[:] = v / ln * 0.5 + 0.5
        m = tile(sl["img_metal"])
        if m is not None:
            mask[..., 0] = m[..., 0]
        g = tile(sl["img_rough"])
        if g is not None:
            gg = 1.0 - g[..., 0] if sl["gmode"] == "ROUGH" else g[..., 0]
            mask[..., 3] = np.clip(gg * sl["gmult"], 0.0, 1.0)
        e = tile(sl["img_emis"])
        if e is not None:
            emis[:] = np.clip(e[..., :3] * sl["emis_str"], 0.0, 1.0)
    return {"base": np.clip(base, 0, 1), "normal": normal, "mask": mask, "emis": emis}


def bake_context(p):
    """(atlas, ext) del edificio: los guardados al generarlo, los de sus objetos, o los por defecto."""
    atlas, ext = p.get("_atlas"), p.get("_ext")
    if not atlas or not ext:
        ob = bpy.data.objects.get(f"{p.get('building_name', 'Residencial01')}_LOD0")
        if ob is not None and ob.get("cs2_atlas"):
            try:
                atlas = {k: tuple(v) for k, v in json.loads(ob["cs2_atlas"]).items()}
                ext = float(ob.get("cs2_ext", ATLAS_SCALE))
            except Exception:
                atlas = None
    return (atlas or ATLAS), (ext or ATLAS_SCALE)


def texture_arrays(p, size):
    """Juego de texturas del edificio (un PNG por mapa, todas del mismo tamaño) como arreglos RGBA 0-1."""
    import numpy as np
    mc = dict(MAT_COLORS)
    mc.update(p.get("colors") or {})
    cols = {"facade": _srgb(p["facade_color"]), "trim": _srgb(mc["trim"]),
            "roof": _srgb(mc["roof"]), "door": _srgb(mc["door"]), "gls": _srgb(mc["gls"]),
            "facb": _srgb(mc["facb"]), "facc": _srgb(mc["facc"]), "facd": _srgb(mc["facd"]),
            "gar": _srgb(mc["gar"]), "rail": _srgb(mc["rail"])}
    atlas, ext = bake_context(p)
    slots = p.get("matslots") or {}
    base = np.ones((size, size, 4), np.float32)
    base[..., :3] = cols["facade"]                     # el espacio entre sectores toma el color de fachada
    ctrl = np.zeros((size, size, 4), np.float32)
    ctrl[..., 3] = 1.0                                 # RGB = máscaras de color, A = quita nieve
    mask = np.zeros((size, size, 4), np.float32)       # R metálico, G capa, B sin uso, A brillo
    mask[..., 3] = ATLAS_GLOSS["facade"]
    normal = np.ones((size, size, 4), np.float32)
    normal[..., 0] = normal[..., 1] = 0.5              # normal plana (OpenGL)
    emis = np.zeros((size, size, 4), np.float32)
    emis[..., 3] = 1.0
    for name, (u0, v0, u1, v1) in atlas.items():
        x0, x1, y0, y1 = int(u0 * size), int(u1 * size), int(v0 * size), int(v1 * size)
        W, H = x1 - x0, y1 - y0
        if W < 2 or H < 2:
            continue
        ppm = ext * size                               # píxeles por metro (ext = UV por metro)
        sl = slots.get(name)
        mp = slot_maps(name, sl, cols[name], W, H, ppm)
        base[y0:y1, x0:x1, :3] = mp["base"]
        normal[y0:y1, x0:x1, :3] = mp["normal"]
        mask[y0:y1, x0:x1] = mp["mask"]
        emis[y0:y1, x0:x1, :3] = mp["emis"]
        cm = int((sl or {}).get("cmask", "0") or 0)
        if cm in (1, 2, 3):
            ctrl[y0:y1, x0:x1, cm - 1] = 1.0
        snow = (sl or {}).get("snow", name != "roof")
        ctrl[y0:y1, x0:x1, 3] = 1.0 if snow else 0.0
    # el espacio entre sectores se rellena con el borde de cada sector (no con el color de fachada): así, al achicarse la imagen
    # (mipmaps del visor y del juego), cada material solo se mezcla consigo mismo
    pad = int(math.ceil(ATLAS_MARGIN * size))
    filled = np.zeros((size, size), bool)
    boxes = []
    for name, (u0, v0, u1, v1) in atlas.items():
        x0, x1, y0, y1 = int(u0 * size), int(u1 * size), int(v0 * size), int(v1 * size)
        if x1 - x0 >= 2 and y1 - y0 >= 2:
            filled[y0:y1, x0:x1] = True
            boxes.append((x0, x1, y0, y1))
    for x0, x1, y0, y1 in boxes:
        wx0, wx1, wy0, wy1 = max(0, x0 - pad), min(size, x1 + pad), max(0, y0 - pad), min(size, y1 + pad)
        free = ~filled[wy0:wy1, wx0:wx1]
        for arr in (base, ctrl, mask, normal, emis):
            ext_blk = np.pad(arr[y0:y1, x0:x1], ((y0 - wy0, wy1 - y1), (x0 - wx0, wx1 - x1), (0, 0)), mode="edge")
            win = arr[wy0:wy1, wx0:wx1]
            win[free] = ext_blk[free]
    return {"BaseColor": base, "ControlMask": ctrl, "MaskMap": mask, "Normal": normal, "Emissive": emis}


def _preview_nodes(m, imgs):
    """Conecta (o desconecta) las texturas del atlas al Principled de un material, solo para verlas en Blender."""
    if m.node_tree is None:
        m.use_nodes = True
    nt = m.node_tree
    bsdf = nt.nodes.get("Principled BSDF")
    if bsdf is None:
        return
    for nd in [nd for nd in nt.nodes if nd.name.startswith("cs2_prev")]:
        nt.nodes.remove(nd)
    if imgs is None:
        if m.get("cs2_prev_on"):
            m["cs2_prev_on"] = False
            bsdf.inputs["Metallic"].default_value = 0.0
            bsdf.inputs["Roughness"].default_value = 0.8
        return
    x, y = bsdf.location.x - 700, bsdf.location.y

    def tex(name, img, dy):
        nd = nt.nodes.new("ShaderNodeTexImage")
        nd.name = nd.label = name
        nd.image = img
        nd.interpolation = "Linear"
        nd.location = (x, y + dy)
        return nd
    b = tex("cs2_prev_base", imgs["BaseColor"], 300)
    nt.links.new(b.outputs["Color"], bsdf.inputs["Base Color"])
    n = tex("cs2_prev_normal", imgs["Normal"], 0)
    nm = nt.nodes.new("ShaderNodeNormalMap")
    nm.name = "cs2_prev_normalmap"
    nm.location = (x + 300, y)
    nt.links.new(n.outputs["Color"], nm.inputs["Color"])
    nt.links.new(nm.outputs["Normal"], bsdf.inputs["Normal"])
    k = tex("cs2_prev_mask", imgs["MaskMap"], -300)
    sep = nt.nodes.new("ShaderNodeSeparateColor")
    sep.name = "cs2_prev_sep"
    sep.location = (x + 300, y - 300)
    nt.links.new(k.outputs["Color"], sep.inputs["Color"])
    nt.links.new(sep.outputs["Red"], bsdf.inputs["Metallic"])
    inv = nt.nodes.new("ShaderNodeMath")
    inv.name = "cs2_prev_inv"
    inv.operation = "SUBTRACT"
    inv.inputs[0].default_value = 1.0
    inv.location = (x + 300, y - 450)
    nt.links.new(k.outputs["Alpha"], inv.inputs[1])
    nt.links.new(inv.outputs["Value"], bsdf.inputs["Roughness"])
    m["cs2_prev_on"] = True


def update_preview(p):
    """Genera el atlas (a 1024 como mucho) y lo pone en los materiales del edificio para verlo en Blender."""
    slots = p.get("matslots") or {}
    on = bool(p.get("pbr_preview")) and any(v.get("mode") == "PBR" for v in slots.values())
    name = p.get("building_name", "Residencial01")
    imgs = None
    if on:
        size = min(int(p.get("pbr_prev_size", 1024)), int(p.get("tex_size", 1024)))
        arr = texture_arrays(p, size)
        imgs = {}
        for key in ("BaseColor", "Normal", "MaskMap"):
            nm = f"CS2_prev_{name}_{key}"
            img = bpy.data.images.get(nm)
            if img is not None and img.size[0] != size:
                bpy.data.images.remove(img)
                img = None
            if img is None:
                img = bpy.data.images.new(nm, size, size, alpha=True)
            img.colorspace_settings.name = "sRGB" if key == "BaseColor" else "Non-Color"
            img.alpha_mode = "CHANNEL_PACKED" if key == "MaskMap" else "STRAIGHT"
            img.pixels.foreach_set(arr[key].ravel())
            img.update()
            imgs[key] = img
    for mn in MAT_ORDER:
        if mn in ("glass", "gls"):
            continue
        m = bpy.data.materials.get(f"{name}_{mn}")
        if m is not None:
            _preview_nodes(m, imgs)


_IMG_EXT = (".png", ".jpg", ".jpeg", ".tga", ".tif", ".tiff", ".bmp", ".webp")
_MAP_TOKENS = (
    ("img_color", ("_color", "_basecolor", "_base_color", "_albedo", "_diffuse", "_diff", "_col")),
    ("img_normal", ("_normalgl", "_normal_gl", "_nor_gl", "_normaldx", "_normal_dx", "_nor_dx", "_normal", "_nor")),
    ("img_rough", ("_roughness", "_rough", "_glossiness", "_gloss", "_specular", "_spec")),
    ("img_metal", ("_metalness", "_metallic", "_metal")),
    ("img_ao", ("_ambientocclusion", "_ao", "_occlusion")),
    ("img_emis", ("_emission", "_emissive", "_emit")),
)


def scan_pbr_set(folder):
    """Busca en una carpeta los mapas de un set PBR (AmbientCG y similares). Devuelve dict de campos del material."""
    out = {}
    try:
        files = sorted(fn for fn in os.listdir(folder) if fn.lower().endswith(_IMG_EXT))
    except OSError:
        return out
    stems = [(fn, os.path.splitext(fn)[0].lower()) for fn in files]
    for key, tokens in _MAP_TOKENS:
        for tok in tokens:
            hit = next((fn for fn, st in stems if st.endswith(tok)), None)
            if hit:
                out[key] = os.path.join(folder, hit)
                if key == "img_normal":
                    out["flipg"] = "dx" in tok
                if key == "img_rough":
                    out["gmode"] = "ROUGH" if tok in ("_roughness", "_rough") else "GLOSS"
                break
    return out


_LIB_CACHE = {}
_PBR_ITEMS = [("", "(sin biblioteca)", "")]


def scan_pbr_library(root):
    """[(ruta relativa, etiqueta)] de los sets PBR de una carpeta (hasta 3 niveles de profundidad)."""
    root = bpy.path.abspath(root or "")
    if not root or not os.path.isdir(root):
        return []
    try:
        stamp = os.path.getmtime(root)
    except OSError:
        stamp = 0
    hit = _LIB_CACHE.get(root)
    if hit and hit[0] == stamp:
        return hit[1]
    items = []

    def visit(path, rel, depth):
        try:
            entries = sorted(os.scandir(path), key=lambda e: e.name.lower())
        except OSError:
            return
        if any(e.is_file() and e.name.lower().endswith(_IMG_EXT) and "_color" in e.name.lower() for e in entries):
            items.append((rel or os.path.basename(path), (rel or os.path.basename(path)).replace("/", " / ")))
            return
        if depth >= 3:
            return
        for e in entries:
            if e.is_dir():
                visit(e.path, f"{rel}/{e.name}" if rel else e.name, depth + 1)
    visit(root, "", 0)
    _LIB_CACHE[root] = (stamp, items)
    return items


def texture_problems(s):
    """Mapas de los materiales con texturas que no se pueden usar: [(material, mapa, ruta, motivo)].
    Sin esto el material caía en silencio a color liso y no se sabía por qué."""
    out = []
    for sl in PBR_SLOTS:
        if getattr(s, f"ms_{sl}_mode") != "PBR":
            continue
        if not getattr(s, f"ms_{sl}_img_color"):
            out.append((PBR_SLOT_LABEL[sl], "Difuso (color)", "", "no tiene imagen de color: se usa el color liso"))
        for k, lab in PBR_MAP_FIELDS:
            path = getattr(s, f"ms_{sl}_{k}")
            if not path:
                continue
            ap = bpy.path.abspath(path)
            if not os.path.isfile(ap):
                out.append((PBR_SLOT_LABEL[sl], lab, path, "no existe el archivo"))
            elif not ap.lower().endswith(_IMG_EXT):
                out.append((PBR_SLOT_LABEL[sl], lab, path, "formato de imagen no reconocido"))
    return out


def texture_problem_text(probs, limit=2):
    """Resumen corto de los problemas, para avisos y la lista de control."""
    if not probs:
        return ""
    parts = [f"{m} · {lab}" + (f" ({why})" if why else "") for m, lab, _p, why in probs[:limit]]
    more = len(probs) - limit
    return "; ".join(parts) + (f" y {more} más" if more > 0 else "")


def apply_pbr_set(s, slot, folder):
    """Pone en un material los mapas de la carpeta; devuelve cuántos encontró."""
    found = scan_pbr_set(folder)
    maps = [k for k, _l in PBR_MAP_FIELDS if k in found]
    live = s.live_update
    s.live_update = False
    try:
        for k, _l in PBR_MAP_FIELDS:
            setattr(s, f"ms_{slot}_{k}", found.get(k, ""))
        if "flipg" in found:
            setattr(s, f"ms_{slot}_flipg", bool(found["flipg"]))
        if "gmode" in found:
            setattr(s, f"ms_{slot}_gmode", found["gmode"])
        setattr(s, f"ms_{slot}_set", os.path.basename(os.path.normpath(folder)))
        setattr(s, f"ms_{slot}_mode", "PBR" if maps else "FLAT")
    finally:
        s.live_update = live
    return len(maps)


def write_textures(folder, asset, p, size=1024):
    """Escribe <asset>_BaseColor/ControlMask/MaskMap/Normal/Emissive.png (PNG de 8 bits, tamaño potencia de 2)."""
    return write_arrays(folder, asset, texture_arrays(p, size), size)


def write_arrays(folder, base, arrays, size):
    os.makedirs(folder, exist_ok=True)
    paths = []
    for mapname, arr in arrays.items():
        img = bpy.data.images.new(f"cs2_tmp_{mapname}", size, size, alpha=True)
        try:
            img.colorspace_settings.name = "Non-Color"   # se guardan los valores tal cual
            img.alpha_mode = "STRAIGHT"
            img.pixels.foreach_set(arr.ravel())
            path = os.path.join(folder, f"{base}_{mapname}.png")
            img.filepath_raw = path
            img.file_format = "PNG"
            img.save()
            paths.append(path)
        finally:
            bpy.data.images.remove(img)
    return paths


TEX_SUFFIXES = ("BaseColor", "ControlMask", "MaskMap", "Normal", "Emissive")


def write_shared_settings(folder, asset, p, lods):
    """settings.json (wiki 'Texture Sharing'): los LOD / otro asset reutilizan texturas en vez de llevar copias."""
    other = (p.get("tex_share_asset") or "").strip()
    shared = {}
    for lod in lods:
        base = f"{asset}_LOD{lod}"
        for suf in TEX_SUFFIXES:
            if other:
                shared[f"{base}_{suf}.png"] = f"../{other}/{other}_{suf}.png"
            else:      # siempre: el juego exige el settings.json cuando un LOD usa las texturas del principal
                shared[f"{base}_{suf}.png"] = f"{asset}_{suf}.png"
    path = os.path.join(folder, "settings.json")
    if shared:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"sharedAssets": shared}, fh, indent=2)
        return path
    if os.path.isfile(path):
        try:
            os.remove(path)
        except OSError:
            pass
    return None


def _np_image(name, arr, size):
    img = bpy.data.images.new(name, size, size, alpha=True)
    img.colorspace_settings.name = "Non-Color"
    img.alpha_mode = "CHANNEL_PACKED"
    img.pixels.foreach_set(arr.astype("float32").ravel())
    img.update()
    return img


def _resize_bilinear(a, size):
    """Agranda un arreglo 2D a size x size con interpolación bilineal (solo numpy)."""
    import numpy as np
    h, w = a.shape
    ys = np.clip((np.arange(size) + 0.5) * h / size - 0.5, 0, h - 1)
    xs = np.clip((np.arange(size) + 0.5) * w / size - 0.5, 0, w - 1)
    y0, x0 = np.floor(ys).astype(int), np.floor(xs).astype(int)
    y1, x1 = np.minimum(y0 + 1, h - 1), np.minimum(x0 + 1, w - 1)
    fy, fx = (ys - y0)[:, None], (xs - x0)[None, :]
    top = a[y0][:, x0] * (1 - fx) + a[y0][:, x1] * fx
    bot = a[y1][:, x0] * (1 - fx) + a[y1][:, x1] * fx
    return (top * (1 - fy) + bot * fy).astype(np.float32)


def _hammersley_dirs(rays):
    """Hemisferio ponderado por coseno (Hammersley): lista de (x, y, z) con z hacia la normal."""
    dirs = []
    for i in range(rays):
        bits, k, v = 0.0, 0.5, i + 1
        while v:
            bits += k * (v & 1)
            v >>= 1
            k *= 0.5
        r, ph = math.sqrt((i + 0.5) / rays), 2.0 * math.pi * bits
        dirs.append((r * math.cos(ph), r * math.sin(ph), math.sqrt(max(0.0, 1.0 - (i + 0.5) / rays))))
    return dirs


def _occluder_bvh(me):
    """BVH con las caras que tapan la luz (el vidrio no)."""
    from mathutils.bvhtree import BVHTree
    me.calc_loop_triangles()
    verts = [v.co.copy() for v in me.vertices]
    tris = [tuple(t.vertices) for t in me.loop_triangles if me.polygons[t.polygon_index].material_index not in SUB_MATS]
    if not tris:
        return None
    return BVHTree.FromPolygons(verts, tris, epsilon=1e-5)


def _ao_at(bvh, dirs, P, nrm, dist, rot=0.0):
    """Oclusión (0 = tapado, 1 = libre) en un punto de la superficie."""
    from mathutils import Vector
    ex = abs(nrm.x) < 0.9
    tg = nrm.cross(Vector((1.0, 0.0, 0.0)) if ex else Vector((0.0, 1.0, 0.0))).normalized()
    bt = nrm.cross(tg)
    cr, sr = math.cos(rot), math.sin(rot)
    origin = P + nrm * 2e-3
    hit = 0.0
    inv = 1.0 / max(dist, 1e-6)
    for dx, dy, dz in dirs:
        d = tg * (dx * cr - dy * sr) + bt * (dx * sr + dy * cr) + nrm * dz
        r_ = bvh.ray_cast(origin, d, dist)
        if r_[0] is not None and r_[1].dot(d) < 0.0:   # una cara vista por detrás = el punto está dentro de otro bloque: no cuenta
            # el tapado pesa menos cuanto más lejos está el obstáculo: sombras suaves, sin bloques negros duros
            x_ = min(1.0, r_[3] * inv)
            hit += 1.0 - x_ * x_ ** 0.5
    return 1.0 - hit / len(dirs)


def compute_ao_map(me, uv_name, size, dist, rays, only=None):
    """Oclusión ambiental (1 = sin sombra) como imagen size x size sobre la UV `uv_name` de la malla.

    Lanza rayos con el BVH de Blender desde una grilla gruesa (la oclusión es suave) y la agranda SIN mezclar
    islas: cada texel final solo toma muestras de su propia cara (así las sombras oscuras de los costados de las
    ventanas no manchan la pared de al lado). Los huecos entre islas se rellenan para que el margen no oscurezca.
    `only`: conjunto de polígonos a rasterizar (los demás comparten isla con alguno de ellos)."""
    import numpy as np
    bvh = _occluder_bvh(me)
    if bvh is None:
        return np.ones((size, size), np.float32)
    uv = np.empty(len(me.loops) * 2, np.float32)
    me.uv_layers[uv_name].data.foreach_get("uv", uv)
    uv = uv.reshape(-1, 2)
    verts = [v.co.copy() for v in me.vertices]
    tri_data = []
    for t in me.loop_triangles:
        poly = me.polygons[t.polygon_index]
        if poly.material_index in SUB_MATS:
            continue
        if only is not None and t.polygon_index not in only:
            continue
        tri_data.append((tuple(t.loops), tuple(t.vertices), poly.normal.copy(), t.polygon_index))
    G = int(max(32, min(384, size // 4)))
    ao_lo = np.zeros((G, G), np.float32)
    own_lo = np.full((G, G), -1, np.int32)
    own_hi = np.full((size, size), -1, np.int32)
    dirs = _hammersley_dirs(rays)

    def raster(pts, N):
        """Texels (ix, iy, w0, w1, w2) de un triángulo en una grilla N x N."""
        pts = pts * N
        x0, x1 = int(max(0, math.floor(pts[:, 0].min()))), int(min(N - 1, math.ceil(pts[:, 0].max())))
        y0, y1 = int(max(0, math.floor(pts[:, 1].min()))), int(min(N - 1, math.ceil(pts[:, 1].max())))
        if x1 < x0 or y1 < y0:
            return None
        (ax, ay), (bx, by), (cx, cy) = pts
        den = (by - cy) * (ax - cx) + (cx - bx) * (ay - cy)
        if abs(den) < 1e-9:
            return None
        gx, gy = np.meshgrid(np.arange(x0, x1 + 1) + 0.5, np.arange(y0, y1 + 1) + 0.5)
        w0 = ((by - cy) * (gx - cx) + (cx - bx) * (gy - cy)) / den
        w1 = ((cy - ay) * (gx - cx) + (ax - cx) * (gy - cy)) / den
        w2 = 1.0 - w0 - w1
        ins = (w0 >= 0) & (w1 >= 0) & (w2 >= 0)
        iy, ix = np.nonzero(ins)
        return x0 + ix, y0 + iy, w0[iy, ix], w1[iy, ix], w2[iy, ix]

    for loops, vi, nrm, pi in tri_data:
        pts = uv[list(loops)]
        pa, pb, pc = (verts[i] for i in vi)
        # mapa de pertenencia a resolución final (para no mezclar islas al agrandar)
        hi = raster(pts, size)
        if hi is not None:
            sel = own_hi[hi[1], hi[0]] < 0
            own_hi[hi[1][sel], hi[0][sel]] = pi
        lo = raster(pts, G)
        if lo is None or len(lo[0]) == 0:
            # triángulo más chico que un texel: usa el texel de su centro
            cxp, cyp = int(min(G - 1, max(0, pts[:, 0].mean() * G))), int(min(G - 1, max(0, pts[:, 1].mean() * G)))
            if own_lo[cyp, cxp] >= 0:
                continue
            lo = (np.array([cxp]), np.array([cyp]), np.array([1 / 3]), np.array([1 / 3]), np.array([1 / 3]))
        for tx, ty, w0, w1, w2 in zip(*lo):
            if own_lo[ty, tx] >= 0:
                continue
            P = pa * float(w0) + pb * float(w1) + pc * float(w2)
            rot = ((int(tx) * 73856093) ^ (int(ty) * 19349663)) % 1000 / 1000.0 * 2.0 * math.pi
            ao_lo[ty, tx] = _ao_at(bvh, dirs, P, nrm, dist, rot)
            own_lo[ty, tx] = pi
    # agrandar tomando solo muestras de la misma cara
    ys = (np.arange(size) + 0.5) * G / size - 0.5
    xs = (np.arange(size) + 0.5) * G / size - 0.5
    y0 = np.clip(np.floor(ys).astype(int), 0, G - 1)
    x0 = np.clip(np.floor(xs).astype(int), 0, G - 1)
    y1, x1 = np.minimum(y0 + 1, G - 1), np.minimum(x0 + 1, G - 1)
    fy = np.clip(ys - np.floor(ys), 0, 1)[:, None]
    fx = np.clip(xs - np.floor(xs), 0, 1)[None, :]
    num = np.zeros((size, size), np.float32)
    den = np.zeros((size, size), np.float32)
    for yy, wy in ((y0, 1 - fy), (y1, fy)):
        for xx, wx in ((x0, 1 - fx), (x1, fx)):
            o = own_lo[yy][:, xx]
            w = (wy * wx) * ((o == own_hi) & (own_hi >= 0))
            num += w * ao_lo[yy][:, xx]
            den += w
    ao = np.where(den > 1e-6, num / np.maximum(den, 1e-6), np.nan).astype(np.float32)
    # texels sin ninguna muestra propia (caras diminutas): promedio de su cara
    miss = np.isnan(ao) & (own_hi >= 0)
    if miss.any():
        nid = int(max(own_hi.max(), own_lo.max())) + 1
        ids = own_lo[own_lo >= 0]
        s_ = np.bincount(ids, weights=ao_lo[own_lo >= 0], minlength=nid)
        c_ = np.bincount(ids, minlength=nid)
        mean = np.where(c_ > 0, s_ / np.maximum(c_, 1), 1.0)
        ao[miss] = mean[own_hi[miss]]
    cov = (~np.isnan(ao)).astype(np.float32)
    ao = np.nan_to_num(ao, nan=0.0)
    for _ in range(max(2, min(12, int(me.get("cs2_bake_margin", 8))))):   # margen: la sombra de la isla vecina no entra
        acc = np.zeros_like(ao)
        cnt = np.zeros_like(ao)
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                acc += np.roll(np.roll(ao * cov, dy, 0), dx, 1)
                cnt += np.roll(np.roll(cov, dy, 0), dx, 1)
        fill = (cov == 0) & (cnt > 0)
        ao[fill] = (acc / np.maximum(cnt, 1))[fill]
        cov[fill] = 1.0
    ao[cov == 0] = 1.0
    return ao


def _plane_pts(me, poly):
    """Coordenadas planas (m) de los vértices de una cara: a lo largo de la pared y altura, o x/y si es horizontal."""
    from mathutils import Vector
    n = poly.normal
    vs = [me.vertices[me.loops[li].vertex_index].co for li in poly.loop_indices]
    if abs(n.z) < 0.7:
        t = Vector((0, 0, 1)).cross(n)
        if t.length > 1e-6:
            t.normalize()
        return [(v.dot(t), v.z) for v in vs]
    return [(v.x, v.y) for v in vs]


# Tolerancias y umbrales del armado de UV únicas (un solo lugar para ajustarlos)
def uv_front_dir(me):
    """Dirección del frente: la normal media de las puertas y portones (si no hay, el lado -Y del generador)."""
    from mathutils import Vector as _V
    acc = _V((0.0, 0.0, 0.0))
    for poly in me.polygons:
        if poly.material_index in (MAT["door"], MAT["gar"]) and abs(poly.normal.z) < 0.5:
            acc += poly.normal
    return acc.normalized() if acc.length > 1e-6 else _V((0.0, -1.0, 0.0))


def uv_zone_tags(poly, front, floor_h):
    """Zonas a las que pertenece una cara: 'ground' (planta baja), 'front' (fachada del frente), 'door' (puertas y portones)."""
    tags = set()
    if poly.center.z < floor_h:
        tags.add("ground")
    if abs(poly.normal.z) < 0.7 and poly.normal.dot(front) > 0.7:
        tags.add("front")
    if poly.material_index in (MAT["door"], MAT["gar"]):
        tags.add("door")
    return tags


def uv_pins_load(raw):
    """JSON de zonas fijadas -> lista de entradas {'m': material, 'c': centro, 'v': [[x, y, z, u, v]...], 'r': índice de su isla madre}."""
    if not raw:
        return []
    try:
        d = json.loads(raw)
        return d if isinstance(d, list) else []
    except Exception:
        _log_exc("zonas_fijadas")
        return []


def uv_pins_match(me, pins, tol=0.02):
    """{índice de entrada: polígono de la malla actual} para las entradas que todavía tienen su cara (mismo material, centro y lados)."""
    from mathutils import kdtree
    out = {}
    if not pins:
        return out
    kd = kdtree.KDTree(len(me.polygons))
    for poly in me.polygons:
        kd.insert(poly.center, poly.index)
    kd.balance()
    used = set()
    for k, e in enumerate(pins):
        for _co, pi, dist in kd.find_n(e["c"], 4):
            if dist > tol:
                break
            poly = me.polygons[pi]
            if pi not in used and poly.material_index == e["m"] and len(poly.vertices) == len(e["v"]):
                out[k] = poly
                used.add(pi)
                break
    return out


def uv_pins_apply(me, uvd, poly, e):
    """Devuelve a una cara fijada sus UV guardadas (cada esquina busca su vértice más cercano en lo guardado)."""
    for li in poly.loop_indices:
        co = me.vertices[me.loops[li].vertex_index].co
        best = min(e["v"], key=lambda r_: (r_[0] - co.x) ** 2 + (r_[1] - co.y) ** 2 + (r_[2] - co.z) ** 2)
        uvd[li].uv = (best[3], best[4])


# componentes de la ocupación en la textura: clave de la propiedad uv_c_<clave> -> materiales que abarca
UV_COMPONENTS = {"wall": ("facade", "facb", "facc", "facd"), "trim": ("trim",), "roof": ("roof",), "door": ("door",),
                 "gar": ("gar",), "rail": ("rail",)}
UV_COMP_NAMES = {"wall": "Paredes", "trim": "Molduras y marcos", "roof": "Techo", "door": "Puertas", "gar": "Portones", "rail": "Barandas"}
UV_COMP_OF_MAT = {MAT[m]: k for k, ms in UV_COMPONENTS.items() for m in ms}

UV_CFG = {
    "lod_wt": (1.0, 0.35),            # densidad relativa del LOD0 y del LOD1 cuando comparten un mapa (el LOD2 lleva el suyo)
    "shape_q": 0.03,         # m: cuantización del contorno para decidir el lado largo de una isla
    "phase_step": 0.015,     # m: resolución de la fase del patrón (ladrillo) al comparar caras
    "geo_tol": 0.02,         # m: dos caras "iguales" pueden diferir esto en cada punto
    "ao_dirs": 10,           # rayos por punto al comparar las sombras de dos caras
    "ao_rot": 0.37,          # giro fijo de los rayos (para que dos contextos iguales den la misma firma)
    "share_max_area": 12.0,  # m²: las caras más grandes no comparten isla
    "crop_max_area": 40.0,   # m²: tope para que una tira use un tramo de otra
    "crop_rect_tol": 0.03,   # proporción de error para considerar rectangular una cara
    "crop_h_tol": 0.02,      # m: diferencia de alto admitida entre tiras
    "merge_min_fill": 0.45,  # una isla fusionada muy hueca se deshace (mejor cara por cara)
    "merge_dot": 0.9995,     # coplanaridad mínima (producto punto de normales) para fusionar
    "merge_plane": 0.01,     # m: distancia máxima entre planos para fusionar
    "hidden_wt": 0.3,        # densidad de lo que no se ve desde ningún lado
    "roof_share": 0.14,      # fracción máxima del mapa que ocupa la azotea plana
    "roof_min_wt": 0.12,     # densidad mínima de la azotea
    "pad_div": 128.0,        # espacio entre islas = textura / 128 px (8 a 1K, 16 a 2K, 32 a 4K): el mipmap no mezcla materiales vecinos
    "notch_fill": 0.85,      # una isla que llena menos que esto de su caja puede dejar huecos aprovechables
    "notch_min_box": 4.0,    # m²: caja mínima para buscar huecos
    "notch_min_part": 0.08,  # fracción mínima de la caja que debe tener un hueco
    "pack_budget_s": 10.0,   # s: tiempo máximo de empaque
}


class _Unwrapper:
    """UV únicas para el horneo (ver `unique_unwrap`). Cada paso del armado es un método:
    collect -> build_items -> share_equal -> share_crops -> merge_free -> roof_budget -> pack -> write."""

    def __init__(self, me, uv_name, size, p, vis=None):
        self.me, self.uv_name, self.size, self.p, self.vis = me, uv_name, size, p, vis
        self.margin = int(p.get("bake_margin", 8))
        self.share = bool(p.get("bake_share", True))
        self.dist = float(p.get("bake_dist", 2.5))
        self.loose = float(p.get("bake_share_loose", 0.15))
        self.share_area = float(p.get("bake_share_area", UV_CFG["share_max_area"]))
        self.align = bool(p.get("bake_align", True))
        self.align_x = self.align          # las juntas verticales también deben coincidir: si no, el ladrillo se ve corrido entre caras vecinas
        self.uniform = bool(p.get("bake_uniform", True))
        self.roof_wt = float(p.get("bake_roof_wt", 1.0))
        self.cf = {}                      # factor de nitidez por material (ocupación por componente)
        for _k, _ms in UV_COMPONENTS.items():
            for _m in _ms:
                self.cf[MAT[_m]] = max(0.25, min(3.0, float(p.get("uv_c_" + _k, 1.0))))
        self.pins = {}      # las zonas fijadas se quitaron de la interfaz: ya no se aplican
        self.pin_polys = {}              # entrada -> polígono actual (las caras fijadas no entran al empaque)
        self.pin_rects = []              # [x, y, w, h] que ocupan en el mapa
        self.pinfo, self.items = {}, []
        self.parent = {}
        self.n_share = self.n_merged = 0
        self.bvh = self.dirs = None
        self.lod_of, self.lod_wt, self.bvhs = None, (1.0, 1.0, 1.0), None   # mapa compartido por varios LOD (ver make_unique_uv_shared)
        self.uniq, self.byp = [], {}
        self.gap, self.s, self.best = 0.0, 0.0, {}

    # -- 1. datos por cara --------------------------------------------------------------------------------------------
    def collect(self):
        """Por cada cara: contorno plano, fase del patrón, clase, densidad, si está tapada, material y área."""
        me, p, vis, c = self.me, self.p, self.vis, UV_CFG
        atl, ext_ = bake_context(p)
        slots_ = p.get("matslots") or {}
        atlas_uv = next((l for l in me.uv_layers if l.name != self.uv_name), None)
        step = c["phase_step"]
        front = uv_front_dir(me)
        floor_h = float(p.get("floor_h", 3.0))
        self.pin_polys = uv_pins_match(me, self.pins)
        pinned = {pl.index for pl in self.pin_polys.values()}
        for poly in me.polygons:
            if poly.material_index in SUB_MATS or poly.index in pinned:
                continue
            phase = (0, 0)
            reg = ATLAS_BY_MAT.get(poly.material_index, "facade")
            sl_ = slots_.get(reg) or {}
            if atlas_uv is not None and sl_.get("mode") == "PBR":
                if reg not in atl:
                    reg = "facade"
                tl = max(0.05, float(sl_.get("tile", SLOT_DEFAULTS["tile"])))
                u0_, v0_ = atl[reg][0], atl[reg][1]
                rx = min((atlas_uv.data[li].uv[0] - u0_) / ext_ for li in poly.loop_indices)
                ry = min((atlas_uv.data[li].uv[1] - v0_) / ext_ for li in poly.loop_indices)
                fx_, fy_ = _pattern_period(sl_.get("img_color", "")) if sl_.get("img_color") else (1.0, 1.0)
                tly = float(sl_.get("_ty") or tl)
                tlx = float(sl_.get("_tx") or tl)
                Px, Py = max(0.04, tlx * fx_), max(0.04, tly * fy_)      # período real del dibujo, en metros
                if self.align and sl_.get("img_color"):
                    nqx, nqy = max(1, round(Px / step)), max(1, round(Py / step))
                    phase = (round((rx % Px) / step) % nqx if self.align_x else 0, round((ry % Py) / step) % nqy)
            nz = poly.normal.z
            cls = 0 if abs(nz) < 0.7 else (1 if nz > 0 else 2)
            v = 1.0 if vis is None else float(vis.get(poly.index, 1.0))
            # la cáscara (paredes, puertas y molduras verticales) se ve aunque sea por una rendija: nunca queda borrosa
            shell = abs(nz) < 0.7 and (poly.material_index in SHELL_MATS or (poly.material_index == MAT["trim"] and poly.area > 0.05))
            if shell and v <= 0.001:
                v = 1.0
            if self.uniform:
                wt = c["hidden_wt"] if v <= 0.001 else 1.0     # misma densidad en todo lo que se ve; solo lo tapado baja
                if poly.material_index == MAT["roof"] and cls == 1:
                    wt = min(wt, self.roof_wt)                 # tope de la azotea (el reparto fino se hace después)
            else:
                vw = 0.12 if v <= 0.001 else 0.45 + 0.55 * min(1.0, v)     # tapada: casi sin lugar en el mapa
                wt = (1.0, 0.6, 0.4)[cls] * vw
                if poly.material_index in (MAT["door"], MAT["gar"]):   # puertas y portones: lo que más se mira
                    wt = max(wt, 0.9) * 1.5
            pri = 1.0
            if v > 0.001:
                pri = self.cf.get(poly.material_index, 1.0)       # ocupación por componente: misma nitidez en todo el componente
                wt *= pri
            lod_ = self.lod_of[poly.index] if self.lod_of is not None else 0
            wt *= self.lod_wt[lod_]          # los LOD lejanos llevan menos densidad dentro del mismo mapa
            self.pinfo[poly.index] = {"pts": _plane_pts(me, poly), "phase": phase, "cls": cls, "wt": wt, "hid": v <= 0.001,
                                      "mat": poly.material_index, "area": poly.area, "pri": round(pri, 3), "lod": lod_}
        self.parent = {i: i for i in self.pinfo}

    def find(self, i):
        parent = self.parent
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def make_item(self, members, merged):
        """Isla con una o varias caras: contorno normalizado (lado largo en horizontal), tamaño, peso y clave de igualdad."""
        pinfo, q = self.pinfo, UV_CFG["shape_q"]
        allp = [pinfo[i]["pts"] for i in members]
        mn0, mn1 = min(a for pts in allp for a, _b in pts), min(b for pts in allp for _a, b in pts)
        allp = [[(a - mn0, b - mn1) for a, b in pts] for pts in allp]
        w, h = max(a for pts in allp for a, _b in pts), max(b for pts in allp for _a, b in pts)
        w, h = max(w, 0.02), max(h, 0.02)
        if round(h / q) > round(w / q):          # el lado largo en horizontal
            allp = [[(b, w - a) for a, b in pts] for pts in allp]
            w, h = h, w
        P0 = pinfo[members[0]]
        area = sum(pinfo[i]["area"] for i in members)
        wt = sum(pinfo[i]["wt"] * pinfo[i]["area"] for i in members) / max(area, 1e-9)
        if merged:
            key = ("grp", members[0])
        else:
            pts = allp[0]
            area2 = sum(pts[i][0] * pts[(i + 1) % len(pts)][1] - pts[(i + 1) % len(pts)][0] * pts[i][1] for i in range(len(pts)))
            key = (P0["mat"], P0["cls"], P0["phase"], area2 > 0, len(pts), P0["pri"], P0.get("lod", 0))
        return {"pi": members[0], "parts": list(zip(members, allp)), "w": w, "h": h, "key": key, "wt": wt, "area": area,
                "rep": members[0], "hid": P0["hid"], "geo": allp[0],
                "roof": P0["mat"] == MAT["roof"] and P0["cls"] == 1}

    def build_items(self):
        self.items = [self.make_item([i], False) for i in self.pinfo]

    # -- 2. caras iguales comparten isla ------------------------------------------------------------------------------
    def same_shape(self, A, B):
        gtol = UV_CFG["geo_tol"]
        if abs(A["w"] - B["w"]) > gtol or abs(A["h"] - B["h"]) > gtol:
            return False
        return all(any(abs(a - c) <= gtol and abs(b - d) <= gtol for c, d in B["geo"]) for a, b in A["geo"]) and \
            all(any(abs(a - c) <= gtol and abs(b - d) <= gtol for a, b in A["geo"]) for c, d in B["geo"])

    def ao_sig(self, it):
        """Firma de sombra de una cara: la oclusión en su centro y cerca de cada esquina (vértices en orden canónico)."""
        me = self.me
        poly = me.polygons[it["pi"]]
        bvh = self.bvhs[self.pinfo[it["pi"]].get("lod", 0)] if self.bvhs else self.bvh      # la sombra se mide en la malla de su propio LOD
        vs = [me.vertices[me.loops[li].vertex_index].co for li in poly.loop_indices]
        if abs(poly.normal.z) < 0.7:
            from mathutils import Vector as _V
            t_ = _V((0, 0, 1)).cross(poly.normal)
            vs.sort(key=lambda v_: (round(v_.z / 0.02), round(v_.dot(t_) / 0.02)))
        else:
            vs.sort(key=lambda v_: (round(v_.x / 0.02), round(v_.y / 0.02)))
        c = poly.center
        spots = [c] + [c * 0.5 + vs[(k * len(vs)) // 4] * 0.5 for k in range(4)]
        return [_ao_at(bvh, self.dirs, s_, poly.normal, self.dist, UV_CFG["ao_rot"]) for s_ in spots]

    def share_equal(self):
        """Caras con el mismo material, contorno, fase de patrón y sombra usan la misma isla."""
        self.bvh = self.bvhs[0] if self.bvhs else _occluder_bvh(self.me)
        self.dirs = _hammersley_dirs(UV_CFG["ao_dirs"])
        groups = {}
        for it in self.items:
            groups.setdefault(it["key"], []).append(it)
        clusters = []
        for key, mem0 in groups.items():
            if key[0] == "grp":
                continue
            cl = []
            for it in mem0:
                for c_ in cl:
                    if self.same_shape(c_[0], it):
                        c_.append(it)
                        break
                else:
                    cl.append([it])
            clusters += cl
        for mem in clusters:
            if len(mem) < 2 or mem[0]["area"] > self.share_area or self.bvh is None:
                continue
            sigs = [self.ao_sig(it) for it in mem]
            reps = []
            for it, sg in zip(mem, sigs):
                for r_it, r_sg in reps:
                    if max(abs(a - b) for a, b in zip(sg, r_sg)) <= self.loose:
                        it["rep"] = r_it["pi"]
                        self.n_share += 1
                        break
                else:
                    reps.append((it, sg))

    def share_crops(self):
        """Tiras rectangulares de la misma altura: la más corta usa un tramo de la más larga (cornisas, zócalos, paneles)."""
        c = UV_CFG
        users0 = {}
        for it in self.items:
            users0[it["rep"]] = users0.get(it["rep"], 0) + 1
        cg = {}
        for it in self.items:
            if it["rep"] != it["pi"] or len(it["parts"]) != 1 or len(it["geo"]) != 4 or it["key"][0] == "grp":
                continue
            if abs(it["w"] * it["h"] - it["area"]) > c["crop_rect_tol"] * it["area"] or it["area"] > c["crop_max_area"]:
                continue
            cg.setdefault((it["key"][0], it["key"][1], it["key"][2], it["hid"]), []).append(it)
        for lst in cg.values():
            lst.sort(key=lambda i_: (-i_["w"], -i_["h"]))
            targets = []
            for it in lst:
                if users0.get(it["pi"], 1) > 1:
                    targets.append((it, None))       # ya tiene gemelas: sirve de base, no se recorta
                    continue
                sg = None
                for r_it, r_sg in targets:
                    if abs(r_it["h"] - it["h"]) <= c["crop_h_tol"] and it["w"] <= r_it["w"] + c["crop_h_tol"]:
                        if sg is None:
                            sg = self.ao_sig(it)
                        if r_sg is None:
                            r_sg = self.ao_sig(r_it)
                        if abs(sum(sg) / len(sg) - sum(r_sg) / len(r_sg)) <= self.loose:
                            it["rep"] = r_it["pi"]
                            self.n_share += 1
                            break
                else:
                    targets.append((it, None))

    # -- 3. islas grandes ---------------------------------------------------------------------------------------------
    def merge_free(self):
        """Las caras sin gemelas que son vecinas, coplanares y del mismo material forman una sola isla."""
        me, pinfo, c = self.me, self.pinfo, UV_CFG
        users = {}
        for it in self.items:
            users[it["rep"]] = users.get(it["rep"], 0) + 1
        free_ = {it["pi"] for it in self.items if it["rep"] == it["pi"] and users[it["pi"]] == 1
                 and pinfo[it["pi"]].get("lod", 0) == 0}      # en LOD1/LOD2 no se funden: así las caras iguales siguen compartiendo isla
        e2p = {}
        for poly in me.polygons:
            if poly.index in free_:
                for li in poly.loop_indices:
                    e2p.setdefault(me.loops[li].edge_index, []).append(poly.index)
        for pl in e2p.values():
            if len(pl) != 2:
                continue
            i, j = pl
            A, B = pinfo[i], pinfo[j]
            if A["mat"] != B["mat"] or A["cls"] != B["cls"] or A["cls"] == 2 or A["hid"] != B["hid"]:
                continue
            if A["mat"] in (MAT["door"], MAT["gar"]):
                continue
            pi_, pj_ = me.polygons[i], me.polygons[j]
            if pi_.normal.dot(pj_.normal) < c["merge_dot"]:
                continue
            vj = me.vertices[me.loops[pj_.loop_start].vertex_index].co
            vi = me.vertices[me.loops[pi_.loop_start].vertex_index].co
            if abs((vj - vi).dot(pi_.normal)) > c["merge_plane"]:
                continue
            ri, rj = self.find(i), self.find(j)
            if ri != rj:
                self.parent[rj] = ri
        comps = {}
        for i in free_:
            comps.setdefault(self.find(i), []).append(i)
        gone = set()
        newit = []
        # una isla fundida más larga que el mapa a la densidad esperada bajaría la nitidez de todo su material: se deja cara por cara
        vis_area = sum(P["area"] for P in pinfo.values() if not P["hid"] and P["cls"] == 0 and P.get("lod", 0) == 0) or 1.0
        s_est = math.sqrt(0.55 / vis_area)
        max_len = 0.9 * (1.0 - max(2, round(self.size / c["pad_div"])) / float(self.size)) / max(s_est, 1e-6)
        for root, members in comps.items():
            if len(members) < 2:
                continue
            it = self.make_item(sorted(members), True)
            if max(it["w"], it["h"]) > max_len:
                continue
            if it["area"] / max(it["w"] * it["h"], 1e-9) >= c["merge_min_fill"]:     # muy hueca: mejor cara por cara
                newit.append(it)
                gone.update(members)
                self.n_merged += len(members) - 1
        if gone:
            self.items = [it for it in self.items if it["pi"] not in gone] + newit

    # -- 4. densidades ------------------------------------------------------------------------------------------------
    def roof_budget(self):
        """La azotea plana es enorme y casi lisa: su densidad baja hasta ocupar como mucho ~14 % del mapa."""
        c = UV_CFG
        self.uniq = [it for it in self.items if it["rep"] == it["pi"]]
        self.byp = {it["pi"]: it for it in self.items}
        uniq = self.uniq
        if self.p.get("uv_same_density", True):
            # misma nitidez en todo un material (ladrillo de pared, etc.): las prioridades de zona y las diferencias de
            # visibilidad no deben dar zonas borrosas junto a otras nítidas. Cada material toma su peso medio (por área)
            acc = {}
            for it in self.items:
                if it["hid"] or it["roof"]:
                    continue
                m_ = (self.pinfo[it["pi"]].get("lod", 0), self.pinfo[it["pi"]]["mat"])
                a_ = acc.setdefault(m_, [0.0, 0.0])
                a_[0] += it["wt"] * it["area"]
                a_[1] += it["area"]
            for it in self.items:
                if it["hid"] or it["roof"]:
                    continue
                a_ = acc.get((self.pinfo[it["pi"]].get("lod", 0), self.pinfo[it["pi"]]["mat"]))
                if a_ and a_[1] > 1e-9:
                    it["wt"] = a_[0] / a_[1]
        ar_roof = sum(it["w"] * it["h"] for it in uniq if it["roof"] and not it["hid"])
        ar_rest = sum(it["w"] * it["h"] * it["wt"] ** 2 for it in uniq if not it["roof"])
        rf_ = max(0.25, min(3.0, float(self.p.get("uv_c_roof", 1.0))))      # componente «Techo»
        if ar_roof > 1e-6 and self.uniform:
            share_ = min(0.45, c["roof_share"] * rf_ * rf_)
            f_ = max(c["roof_min_wt"], min(self.roof_wt * rf_, math.sqrt(share_ / (1.0 - share_) * ar_rest / ar_roof)))
            for it in uniq:
                if it["roof"] and not it["hid"]:
                    it["wt"] = f_ * self.lod_wt[self.pinfo[it["pi"]].get("lod", 0)]
        for it in self.items:       # la isla compartida toma el mayor peso de quienes la usan
            r_ = self.byp[it["rep"]]
            if r_ is not it and it["wt"] > r_["wt"]:
                r_["wt"] = it["wt"]
        # tope común por componente: una tira larga que no cabe en el mapa no puede quedar más borrosa que sus vecinas del mismo
        # material, así que todo el material baja a lo que esa tira admite (y el resto del mapa aprovecha el lugar)
        pad_ = max(2, round(self.size / UV_CFG["pad_div"]))
        room_ = 1.0 - pad_ / float(self.size)
        caps = {}
        for it in self.uniq:
            if it["hid"] or it["roof"]:
                continue
            pi_ = self.pinfo[it["pi"]]
            k_ = (pi_.get("lod", 0), pi_["mat"])
            caps[k_] = min(caps.get(k_, 1e9), room_ / max(it["w"], it["h"], 1e-6))
        for it in self.items:
            pi_ = self.pinfo[it["pi"]]
            it["mcap"] = caps.get((pi_.get("lod", 0), pi_["mat"]), 1e9) if not (it["hid"] or it["roof"]) else 1e9

    # -- 5. empaque ---------------------------------------------------------------------------------------------------
    def isc(self, it, s):
        """Escala (UV por metro) de una isla: la del reparto, pero sin pasar del mapa (las tiras largas bajan su densidad)."""
        gap = self.gap
        return min(it["wt"] * s, it.get("mcap", 1e9), (1.0 - gap) / max(it["w"], 1e-6), (1.0 - gap) / max(it["h"], 1e-6))

    @staticmethod
    def find_notches(it):
        """Rectángulos vacíos en las esquinas de la caja de una isla cóncava (una L, un marco): otras islas pueden ir ahí."""
        c = UV_CFG
        pts, w_, h_ = it["geo"], it["w"], it["h"]
        if len(it["parts"]) != 1 or len(pts) < 5 or it["area"] > c["notch_fill"] * w_ * h_ or w_ * h_ < c["notch_min_box"]:
            return []

        def inside(x_, y_):
            c_ = False
            for i_ in range(len(pts)):
                (x1, y1), (x2, y2) = pts[i_], pts[(i_ + 1) % len(pts)]
                if (y1 > y_) != (y2 > y_) and x_ < (x2 - x1) * (y_ - y1) / (y2 - y1) + x1:
                    c_ = not c_
            return c_
        xs_ = sorted({round(p_[0], 3) for p_ in pts} | {0.0, w_})
        ys_ = sorted({round(p_[1], 3) for p_ in pts} | {0.0, h_})
        out_ = []
        for cx_, cy_ in ((0.0, 0.0), (w_, 0.0), (0.0, h_), (w_, h_)):
            best_, ba = None, 0.0
            for vx in xs_:
                for vy in ys_:
                    x0, x1 = min(cx_, vx), max(cx_, vx)
                    y0, y1 = min(cy_, vy), max(cy_, vy)
                    ar = (x1 - x0) * (y1 - y0)
                    if ar <= ba or ar < c["notch_min_part"] * w_ * h_ or (x1 - x0) < 0.3 or (y1 - y0) < 0.3:
                        continue
                    if any(x0 + 1e-6 < p_[0] < x1 - 1e-6 and y0 + 1e-6 < p_[1] < y1 - 1e-6 for p_ in pts):
                        continue
                    if any(inside(x0 + (x1 - x0) * (i_ + 0.5) / 8, y0 + (y1 - y0) * (j_ + 0.5) / 8) for i_ in range(8) for j_ in range(8)):
                        continue
                    best_, ba = (x0, y0, x1, y1), ar
            if best_ is not None:
                out_.append(best_)
        return out_

    def attempt(self, s, order):
        """Empaque MaxRects (mejor ajuste por lado corto): cada isla va en el hueco libre que mejor le calza.
        Devuelve {isla: (x, y)} o None si con la escala `s` no entran todas. Lo fijado por el usuario ya ocupa su lugar."""
        gap = self.gap
        bl = self.p.get("uv_grid", True)
        free = [[0.0, 0.0, 1.0, 1.0]]
        pos = {}

        def within(r_, o_):
            return (r_[0] >= o_[0] - 1e-12 and r_[1] >= o_[1] - 1e-12 and
                    r_[0] + r_[2] <= o_[0] + o_[2] + 1e-12 and r_[1] + r_[3] <= o_[1] + o_[3] + 1e-12)

        def carve(free, x, y, ww, hh, extra):
            keep, new = [], []
            for fr in free:
                if x >= fr[0] + fr[2] - 1e-12 or x + ww <= fr[0] + 1e-12 or y >= fr[1] + fr[3] - 1e-12 or y + hh <= fr[1] + 1e-12:
                    keep.append(fr)
                    continue
                if x > fr[0] + 1e-12:
                    new.append([fr[0], fr[1], x - fr[0], fr[3]])
                if x + ww < fr[0] + fr[2] - 1e-12:
                    new.append([x + ww, fr[1], fr[0] + fr[2] - x - ww, fr[3]])
                if y > fr[1] + 1e-12:
                    new.append([fr[0], fr[1], fr[2], y - fr[1]])
                if y + hh < fr[1] + fr[3] - 1e-12:
                    new.append([fr[0], y + hh, fr[2], fr[1] + fr[3] - y - hh])
            new += extra
            # solo los huecos nuevos se comparan con el resto (los viejos ya estaban depurados entre sí)
            new = [r_ for r_ in new if r_[2] > 1e-6 and r_[3] > 1e-6]
            new.sort(key=lambda r_: -(r_[2] * r_[3]))
            nn = []
            for r_ in new:
                if not any(within(r_, o_) for o_ in nn) and not any(within(r_, o_) for o_ in keep):
                    nn.append(r_)
            keep = [o_ for o_ in keep if not any(within(o_, r_) for r_ in nn)]
            free = keep + nn
            if len(free) > 200:
                free.sort(key=lambda r_: -(r_[2] * r_[3]))
                free = free[:200]           # solo los huecos más grandes (acota el costo)
            return free
        for rx, ry, rw, rh in self.pin_rects:
            free = carve(free, rx, ry, rw, rh, [])
        for it in sorted(self.uniq, key=self.orders[order]):
            k_ = self.isc(it, s)
            ww, hh = it["w"] * k_ + gap, it["h"] * k_ + gap
            if ww > 1.0 + 1e-9 or hh > 1.0 + 1e-9:
                return None
            bestf, bs = None, 1e9
            for fr in free:
                if ww <= fr[2] + 1e-9 and hh <= fr[3] + 1e-9:
                    sc_ = (fr[1] + fr[0] * 0.25) if bl else min(fr[2] - ww, fr[3] - hh)    # bl: esquina arriba-izquierda primero (ordenado)
                    if sc_ < bs or (sc_ == bs and fr[1] < bestf[1]):
                        bestf, bs = fr, sc_
            if bestf is None:
                return None
            x, y = bestf[0], bestf[1]
            pos[it["pi"]] = (x, y)
            extra = []
            for nx0, ny0, nx1, ny1 in it["notch"]:     # el hueco de una isla en L vuelve a estar libre (con su margen)
                X0 = x if nx0 <= 1e-9 else x + gap / 2 + nx0 * k_ + gap / 2
                X1 = x + ww if nx1 >= it["w"] - 1e-9 else x + gap / 2 + nx1 * k_ - gap / 2
                Y0 = y if ny0 <= 1e-9 else y + gap / 2 + ny0 * k_ + gap / 2
                Y1 = y + hh if ny1 >= it["h"] - 1e-9 else y + gap / 2 + ny1 * k_ - gap / 2
                if X1 - X0 > 1e-6 and Y1 - Y0 > 1e-6:
                    extra.append([X0, Y0, X1 - X0, Y1 - Y0])
            free = carve(free, x, y, ww, hh, extra)
        return pos

    def attempt_shelf(self, s, order):
        """Empaque ORDENADO en filas (estantes) alineadas a la izquierda y arriba, como un atlas modular:
        las islas se agrupan por material, cada fila tiene islas de altura parecida. Devuelve {isla: (x, y)} o None."""
        gap = self.gap
        pos = {}
        x = y = rowh = 0.0
        last_g = None
        for it in sorted(self.uniq, key=self.orders[order]):
            k_ = self.isc(it, s)
            ww, hh = it["w"] * k_ + gap, it["h"] * k_ + gap
            if ww > 1.0 + 1e-9 or hh > 1.0 + 1e-9:
                return None
            g_ = self.orders[order](it)[0] if isinstance(self.orders[order](it), tuple) else None
            if g_ != last_g and last_g is not None and x > 0:      # otro material: fila nueva
                y, x, rowh = y + rowh, 0.0, 0.0
            last_g = g_
            while True:
                if x + ww > 1.0 + 1e-9:
                    y, x, rowh = y + max(rowh, gap), 0.0, 0.0
                    continue
                hit = None
                for rx, ry, rw, rh in self.pin_rects:
                    if x < rx + rw - 1e-12 and x + ww > rx + 1e-12 and y < ry + rh - 1e-12 and y + hh > ry + 1e-12:
                        hit = rx + rw
                        break
                if hit is None:
                    break
                x = hit
            if y + hh > 1.0 + 1e-9:
                return None
            pos[it["pi"]] = (x, y)
            x += ww
            rowh = max(rowh, hh)
        return pos

    def pack(self):
        """Busca la mayor escala con la que entran todas las islas (bisección), probando varios órdenes de colocación."""
        pad = max(2, round(self.size / UV_CFG["pad_div"]))     # 8 px a 1K, 16 a 2K, 32 a 4K (no depende del margen de horneado)
        self.gap = pad / float(self.size)           # la dilatación del horneo rellena el hueco: no hace falta duplicarlo
        for it in self.uniq:
            it["notch"] = self.find_notches(it)
        self.pin_rects = []
        for k, poly in self.pin_polys.items():
            us = [r_[3] for r_ in self.pins[k]["v"]]
            vs = [r_[4] for r_ in self.pins[k]["v"]]
            x0, y0 = max(0.0, min(us) - self.gap / 2), max(0.0, min(vs) - self.gap / 2)
            x1, y1 = min(1.0, max(us) + self.gap / 2), min(1.0, max(vs) + self.gap / 2)
            if x1 > x0 and y1 > y0:
                self.pin_rects.append((x0, y0, x1 - x0, y1 - y0))
        self.orders = (lambda i: -i["h"] * i["wt"], lambda i: -i["w"] * i["wt"], lambda i: -(i["w"] * i["h"]) * i["wt"] ** 2)
        if self.p.get("uv_grid", True):         # ordenado: primero los paneles grandes, luego los detalles rellenan los huecos
            self.orders = (lambda i: -(i["w"] * i["h"]) * i["wt"] ** 2, lambda i: (-round(i["h"] * i["wt"], 1), -i["w"]),
                           lambda i: -i["h"] * i["wt"])
        if self.p.get("uv_group", True):
            # islas del mismo material juntas (la wiki pide agrupar colores parecidos y separar los distintos): se colocan de a grupos
            gorder = {n: k for k, n in enumerate(ATLAS_ORDER)}

            def grp(it):
                return gorder.get(ATLAS_BY_MAT.get(self.pinfo[it["pi"]]["mat"], "facade"), 99) if it["pi"] in self.pinfo else 99
            self.orders = tuple((lambda i, f=f: (grp(i), f(i))) for f in self.orders)
        budget = UV_CFG["pack_budget_s"]
        t0 = time.time()
        s, best = 0.0, {}
        for oi in range(len(self.orders)):      # se queda el orden que da más píxeles por metro
            lo, hi = max(1e-6, s), 1.0
            cur = None
            attempt = self.attempt
            if oi > 0 and (time.time() - t0 > budget / 2 or attempt(lo, oi) is None):
                continue                    # sin tiempo, o este orden no supera lo que ya se consiguió
            for _ in range(13):
                if time.time() - t0 > budget and cur is not None:
                    break                   # tope de tiempo: se queda con lo logrado
                mid = (lo + hi) / 2
                got = attempt(mid, oi)
                if got is None:
                    hi = mid
                else:
                    lo, cur = mid, got
            if cur is None:
                cur = attempt(lo, oi) or {}
            if lo >= s:
                s, best = lo, cur
        self.s, self.best = s, best

    # -- 6. resultado -------------------------------------------------------------------------------------------------
    def write(self):
        """Escribe las UV en la malla. Devuelve (reps, info)."""
        me, gap, s, size = self.me, self.gap, self.s, self.size
        uvd = me.uv_layers[self.uv_name].data
        for it in self.items:
            r = self.byp[it["rep"]]
            ox, oy = self.best.get(r["pi"], (0.0, 0.0))
            k_ = self.isc(r, s)
            for pidx, pts in it["parts"]:
                poly = me.polygons[pidx]
                for li, (a, b) in zip(poly.loop_indices, pts):
                    uvd[li].uv = (ox + gap / 2 + a * k_, oy + gap / 2 + b * k_)
        reps = {}
        for it in self.items:
            for pidx, _pts in it["parts"]:
                reps[pidx] = pidx if len(it["parts"]) > 1 else it["rep"]
        for k, poly in self.pin_polys.items():          # lo fijado vuelve exactamente a su lugar
            uv_pins_apply(me, uvd, poly, self.pins[k])
            mother = self.pin_polys.get(self.pins[k].get("r", k))
            reps[poly.index] = mother.index if mother is not None else poly.index
        uniq = self.uniq
        used = sum((it["w"] * self.isc(it, s)) * (it["h"] * self.isc(it, s)) for it in uniq)
        clamped = sum(1 for it in uniq if not it["hid"] and self.isc(it, s) < it["wt"] * s * 0.98)
        comp_area, comp_px, comp_lim = {}, {}, set()
        for it in uniq:
            if it["hid"]:
                continue
            ck = UV_COMP_OF_MAT.get(self.pinfo[it["pi"]]["mat"])
            if ck is None or self.pinfo[it["pi"]].get("lod", 0) != 0:
                continue
            k2_ = self.isc(it, s)
            ar2_ = (it["w"] * k2_) * (it["h"] * k2_)
            comp_area[ck] = comp_area.get(ck, 0.0) + ar2_
            comp_px[ck] = comp_px.get(ck, 0.0) + k2_ * size * ar2_
            if k2_ < it["wt"] * s * 0.98:
                comp_lim.add(ck)
        info = {"caras": len(self.pinfo), "islas": len(uniq), "reducidas": clamped, "compartidas": self.n_share,
                "fusionadas": self.n_merged, "px_por_m": round(s * size, 1),
                "ocultas": sum(1 for it in self.items if it["hid"]),
                "comp_pct": {k: round(v * 100.0, 1) for k, v in comp_area.items()},
                "comp_px": {k: round(comp_px[k] / max(comp_area[k], 1e-12), 1) for k in comp_area},
                "comp_limitado": sorted(comp_lim), "uso": round(min(1.0, used + sum(r_[2] * r_[3] for r_ in self.pin_rects)) * 100.0, 1)}
        if self.pins:
            info["fijadas"] = len(self.pin_polys)
            info["fijadas_perdidas"] = len(self.pins) - len(self.pin_polys)
        return reps, info

    def run(self):
        self.collect()
        self.build_items()
        if self.share:
            self.share_equal()
            if self.bvh is not None and self.p.get("bake_crop", True):
                self.share_crops()
        if self.p.get("uv_merge", True):
            self.merge_free()
        self.roof_budget()
        self.pack()
        return self.write()


def unique_unwrap(me, uv_name, size, p, vis=None):
    """UV únicas propias para el horneo: una isla por cara (sin marcos huecos), con islas iguales superpuestas.

    Dos caras comparten isla si tienen el mismo material, el mismo contorno (a 2 cm), la misma fase del patrón y una
    oclusión parecida. Todo lo que se ve tiene la misma densidad (`vis`: {polígono: 0..1} solo detecta lo tapado).
    Devuelve (reps, info): reps = {polígono: polígono que dibuja la isla}."""
    return _Unwrapper(me, uv_name, size, p, vis).run()


def _mesh_sig(me):
    """Huella barata de la geometría: si cambia, las UV y las sombras guardadas ya no valen."""
    import numpy as np
    n = len(me.vertices)
    a = np.empty(n * 3, np.float32)
    me.vertices.foreach_get("co", a)
    return f"{n}:{len(me.polygons)}:{float(a.sum()):.2f}:{float((a * a).sum()):.1f}"


def face_importance(me):
    """Importancia de cada cara para repartir los píxeles: fracción (0 a 1) de las direcciones exteriores desde las
    que se la ve (rayos contra el propio modelo). 0 = nadie la ve (tapada); cerca del suelo vale un poco más."""
    bvh = _occluder_bvh(me)
    out = {}
    if bvh is None:
        return out
    dirs = _view_dirs()[::2]
    for poly in me.polygons:
        if poly.material_index in SUB_MATS:
            continue
        n = poly.normal
        valid = [d for d in dirs if d.dot(n) > 0.12]
        if not valid:
            out[poly.index] = 0.0
            continue
        c = poly.center
        pts = [c]
        if poly.area > 4.0 and len(poly.vertices) >= 3:
            vs = [me.vertices[me.loops[li].vertex_index].co for li in poly.loop_indices]
            pts += [c + (v_ - c) * 0.6 for v_ in vs[:4]]
        free = 0
        for q in pts:
            o = q + n * 0.004
            free += sum(1 for d in valid if bvh.ray_cast(o, d, 1e4)[0] is None)
        v = free / float(len(valid) * len(pts))
        near = 1.0 if c.z < 3.0 else max(0.85, 1.0 - (c.z - 3.0) * 0.015)
        out[poly.index] = min(1.0, v * near) if v > 0 else 0.0
    return out


def _split_hollow_faces(me, min_fill=0.3):
    """Triangula los polígonos que llenan poco su caja (L, marcos, anillos): en el mapa UV cada triángulo ocupa solo su
    propia caja. No cambia la cantidad de triángulos."""
    bm = bmesh.new()
    try:
        bm.from_mesh(me)
        zax_ = Vector((0, 0, 1))
        bad = []
        for f in bm.faces:
            if len(f.verts) <= 4 or f.material_index in SUB_MATS:
                continue
            nrm = f.normal
            if abs(nrm.z) < 0.7:
                t_ = zax_.cross(nrm)
                if t_.length > 1e-6:
                    t_.normalize()
                pts = [(v.co.dot(t_), v.co.z) for v in f.verts]
            else:
                pts = [(v.co.x, v.co.y) for v in f.verts]
            bw = max(q[0] for q in pts) - min(q[0] for q in pts)
            bh = max(q[1] for q in pts) - min(q[1] for q in pts)
            if bw * bh > 1e-6 and f.calc_area() / (bw * bh) < min_fill:
                bad.append(f)
        if bad:
            bmesh.ops.triangulate(bm, faces=bad, quad_method="BEAUTY", ngon_method="BEAUTY")
            bm.to_mesh(me)
            me.update()
        return len(bad)
    finally:
        bm.free()


def bake_dilate_px(p, size):
    """Píxeles que el horneo extiende el color de cada isla: al menos la mitad del espacio entre islas (+2), para que no queden huecos
    de otro color entre ellas (el mipmap del visor y del juego los mezclaría con la isla vecina)."""
    return min(64, max(int(p.get("bake_margin", 8)), max(2, round(size / UV_CFG["pad_div"])) // 2 + 2))


def make_unique_uv(me, p, size):
    """Crea (o rehace) en la malla la capa 'CS2_unique': una isla por cara, compartidas las iguales, con la densidad
    repartida por importancia. Deja la huella en la malla. Devuelve el diccionario de información."""
    import numpy as np
    _split_hollow_faces(me)        # polígonos en L o en marco (cornisas unidas): su caja vacía no debe ocupar el mapa
    atlas = next((l for l in me.uv_layers if l.name != "CS2_unique"), None)
    if atlas is None:
        raise RuntimeError("La malla no tiene UV")
    n = len(atlas.data)
    tmp = np.empty(n * 2, np.float32)
    atlas.data.foreach_get("uv", tmp)
    if "CS2_unique" in me.uv_layers:
        uni = me.uv_layers["CS2_unique"]
    else:
        keep = me.uv_layers.active
        uni = me.uv_layers.new(name="CS2_unique")
        if keep is not None:
            me.uv_layers.active = keep
    uni.data.foreach_set("uv", tmp)
    vis = face_importance(me) if p.get("uv_smart", True) else None
    reps, info = unique_unwrap(me, "CS2_unique", size, p, vis)
    npoly = len(me.polygons)
    me["cs2_reps"] = [int(reps.get(i, i)) for i in range(npoly)]
    uid = "%08x" % random.getrandbits(32)
    info["tam"] = int(size)
    me["cs2_uv"] = json.dumps({"mode": "BAKE", "sig": _mesh_sig(me), "uid": uid, "info": info})
    return info


def tex_shared(p):
    """True si el LOD0 (principal) y el LOD1 comparten UN solo juego de texturas con sombras horneadas (wiki «Texture Sharing»:
    el LOD1 usa las texturas de la malla principal y un settings.json lo declara). El LOD2 lleva sus propias texturas."""
    return bool(p.get("bake_ao"))      # obligatorio: sin el settings.json el juego falla al importar


def make_unique_uv_shared(meshes, p, size):
    """Como `make_unique_uv`, pero las islas del LOD0 y del LOD1 se arman JUNTAS en un único mapa: el LOD1 comparte la
    textura del principal. El LOD1 lleva menos densidad (UV_CFG["lod_wt"]). Devuelve una lista de información por malla."""
    import numpy as np
    for me in meshes:
        _split_hollow_faces(me)
    atlas_name = None
    loops_uv, loops_at = [], []
    verts, faces, mats, lod_of = [], [], [], []
    vis_all, offs = {}, []
    smart = p.get("uv_smart", True)
    for lod, me in enumerate(meshes):
        atlas = next((l for l in me.uv_layers if l.name != "CS2_unique"), None)
        if atlas is None:
            raise RuntimeError("La malla no tiene UV")
        atlas_name = atlas_name or atlas.name
        n = len(atlas.data)
        tmp = np.empty(n * 2, np.float32)
        atlas.data.foreach_get("uv", tmp)
        if "CS2_unique" in me.uv_layers:
            uni = me.uv_layers["CS2_unique"]
        else:
            keep = me.uv_layers.active
            uni = me.uv_layers.new(name="CS2_unique")
            if keep is not None:
                me.uv_layers.active = keep
        uni.data.foreach_set("uv", tmp)
        vis = face_importance(me) if smart else None
        base_v, base_p = len(verts), len(faces)
        offs.append((base_p, len(me.polygons)))
        verts += [tuple(v.co) for v in me.vertices]
        for poly in me.polygons:
            faces.append([base_v + me.loops[li].vertex_index for li in poly.loop_indices])
            mats.append(poly.material_index)
            lod_of.append(lod)
            loops_at += [tuple(atlas.data[li].uv) for li in poly.loop_indices]
        if vis is not None:
            vis_all.update({base_p + i: v for i, v in vis.items()})
    cm = bpy.data.meshes.new("cs2_joint_uv")
    try:
        cm.from_pydata(verts, [], faces)
        cm.update()
        cm.polygons.foreach_set("material_index", mats)
        la = cm.uv_layers.new(name=atlas_name)
        lu = cm.uv_layers.new(name="CS2_unique")
        flat = np.array(loops_at, np.float32).ravel()
        la.data.foreach_set("uv", flat)
        lu.data.foreach_set("uv", flat)
        un = _Unwrapper(cm, "CS2_unique", size, dict(p, uv_pins=""), vis_all if smart else None)
        un.lod_of = lod_of
        w = tuple(UV_CFG["lod_wt"])
        un.lod_wt = (w[0], w[1], w[-1])
        un.bvhs = [_occluder_bvh(m) for m in meshes]
        reps, info = un.run()
        uvall = np.empty(len(lu.data) * 2, np.float32)
        lu.data.foreach_get("uv", uvall)
        uvall = uvall.reshape(-1, 2)
        pos, out = 0, []
        uid = "%08x" % random.getrandbits(32)
        for lod, me in enumerate(meshes):
            base_p, npoly = offs[lod]
            nloops = len(me.loops)
            me.uv_layers["CS2_unique"].data.foreach_set("uv", uvall[pos:pos + nloops].ravel())
            pos += nloops
            rl, first_out = [], {}
            for i in range(npoly):
                r_ = int(reps.get(base_p + i, base_p + i)) - base_p
                if not 0 <= r_ < npoly:       # la gemela es de otro LOD: este LOD hornea una cara por isla (gana el LOD0 al unir)
                    r_ = first_out.setdefault(r_, i)
                rl.append(r_)
            me["cs2_reps"] = rl
            inf = dict(info, caras=npoly, px_por_m=round(un.s * size * un.lod_wt[min(lod, 2)], 1), tam=int(size), compartido=True)
            me["cs2_uv"] = json.dumps({"mode": "BAKE", "sig": _mesh_sig(me), "uid": uid + str(lod), "info": inf})
            out.append(inf)
        return out
    finally:
        bpy.data.meshes.remove(cm)


def uv_mask(me, uv_name, size, margin):
    """Máscara (size x size, fila 0 = abajo) de los texels cubiertos por las islas de una malla, ensanchada `margin` px."""
    import numpy as np
    me.calc_loop_triangles()
    lay = me.uv_layers.get(uv_name) or me.uv_layers[0]      # la malla horneada lleva las UV únicas como UV0
    uvd = lay.data
    m = np.zeros((size, size), bool)
    seen = set()
    mi = me.polygons
    for t in me.loop_triangles:
        if mi[t.polygon_index].material_index in SUB_MATS:      # ventanas y vidrio conservan sus UV: no cuentan
            continue
        pts = [uvd[li].uv for li in t.loops]
        key = tuple(round(c * size) for q in pts for c in q)
        if key in seen:
            continue
        seen.add(key)
        xs = [q[0] * size for q in pts]
        ys = [q[1] * size for q in pts]
        x0, x1 = max(0, int(min(xs)) - 1), min(size - 1, int(max(xs)) + 1)
        y0, y1 = max(0, int(min(ys)) - 1), min(size - 1, int(max(ys)) + 1)
        if x1 < x0 or y1 < y0:
            continue
        gx, gy = np.meshgrid(np.arange(x0, x1 + 1) + 0.5, np.arange(y0, y1 + 1) + 0.5)
        (ax, ay), (bx, by), (cx, cy) = zip(xs, ys)
        d = (by - cy) * (ax - cx) + (cx - bx) * (ay - cy)
        if abs(d) < 1e-12:
            continue
        l1 = ((by - cy) * (gx - cx) + (cx - bx) * (gy - cy)) / d
        l2 = ((cy - ay) * (gx - cx) + (ax - cx) * (gy - cy)) / d
        ins = (l1 >= -0.02) & (l2 >= -0.02) & (1 - l1 - l2 >= -0.02)
        m[y0:y1 + 1, x0:x1 + 1] |= ins
    for _ in range(max(0, int(margin))):
        g = m.copy()
        g[1:] |= m[:-1]
        g[:-1] |= m[1:]
        g[:, 1:] |= m[:, :-1]
        g[:, :-1] |= m[:, 1:]
        m = g
    return m


def composite_lod_textures(meshes, texs, size, p):
    """Une las texturas horneadas de cada LOD en UN solo juego: cada LOD aporta solo el sitio de sus islas (el LOD0 manda
    si algo se pisa). Devuelve {mapa: array}."""
    import numpy as np
    margin = bake_dilate_px(p, size)
    out, claimed = None, np.zeros((size, size), bool)
    for me, tex in zip(meshes, texs):
        mask = uv_mask(me, "CS2_unique", size, margin)
        if out is None:
            out = {k: v.copy() for k, v in tex.items()}
        else:
            sel = mask & ~claimed
            for k, v in tex.items():
                out[k][sel] = v[sel]
        claimed |= mask
    return out


def bake_all_lods(context, p, objs, arrays, name, a=0.0, b=1.0):
    """Hornea las texturas de todos los LOD con sus UV y sombras. Devuelve (texturas, mallas con UV únicas): una textura por LOD,
    o, si el LOD0 y el LOD1 comparten (`tex_shared`), [textura unida del LOD0+LOD1, textura del LOD2]. Quien llama libera las mallas."""
    sizes = bake_lod_sizes(int(p.get("tex_size", 1024)), p)
    texs, umeshes = [], []
    try:
        for lod, ob in enumerate(objs):
            _prog_step(context, a + (b - a) * lod / max(1, len(objs)), f"Horneando texturas del LOD{lod}")
            d_ = uv_state(ob.data)
            ao_ = read_ao(name, lod, d_["uid"] if d_ else None)
            tex, um = bake_lod(context, ob.data, p, arrays, sizes[min(lod, 2)], ao=ao_, have_uv=True)
            texs.append(tex)
            umeshes.append(um)
        if tex_shared(p):
            return [composite_lod_textures(umeshes[:2], texs[:2], sizes[0], p)] + texs[2:], umeshes
        return texs, umeshes
    except Exception:
        for um in umeshes:
            bpy.data.meshes.remove(um)
        raise



def uv_state(me):
    """Estado de las UV guardadas en una malla: None si no hay, o {'mode', 'info', 'uid', 'stale'}."""
    raw = me.get("cs2_uv")
    if not raw:
        return None
    try:
        d = json.loads(raw)
    except ValueError:
        return None
    d["stale"] = d.get("sig") != _mesh_sig(me)
    if d.get("mode") == "BAKE" and "CS2_unique" not in me.uv_layers:
        d["stale"] = True
    return d


def ao_image_name(name, lod):
    return f"CS2_AO_{name}_LOD{lod}"


def store_ao(name, lod, ao, uid):
    """Guarda la oclusión horneada de un LOD como imagen empaquetada en el .blend."""
    import numpy as np
    size = ao.shape[0]
    nm = ao_image_name(name, lod)
    img = bpy.data.images.get(nm)
    if img is not None and (img.size[0] != size or img.size[1] != size):
        bpy.data.images.remove(img)
        img = None
    if img is None:
        img = bpy.data.images.new(nm, size, size, alpha=False)
    img.colorspace_settings.name = "Non-Color"
    buf = np.empty((size, size, 4), np.float32)
    buf[..., :3] = ao[..., None]
    buf[..., 3] = 1.0
    img.pixels.foreach_set(buf.ravel())
    img["cs2_uid"] = uid
    img.use_fake_user = True
    try:
        img.pack()
    except Exception as e:
        print("CS2: no pude empaquetar la oclusión:", e)
    img.update()
    return img


def read_ao(name, lod, uid=None):
    """Oclusión guardada (size x size, 1 = sin sombra) o None si falta o es de otras UV."""
    import numpy as np
    img = bpy.data.images.get(ao_image_name(name, lod))
    if img is None or (uid is not None and img.get("cs2_uid") != uid):
        return None
    w, h = img.size
    if w == 0 or h == 0:
        return None
    a = np.empty(w * h * 4, np.float32)
    img.pixels.foreach_get(a)
    return a.reshape(h, w, 4)[..., 0].copy()


def tex_status(s):
    """Dónde está el flujo de texturas: dict con built, mode, uv, bake, assigned, stale, info."""
    name = s.building_name or "Residencial01"
    st = {"built": False, "lods": True, "mode": "BAKE" if s.bake_ao else "REPEAT", "uv": False, "bake": False,
          "assigned": False, "stale": False, "info": None}
    objs = [bpy.data.objects.get(f"{name}_LOD{i}") for i in range(3)]
    if objs[0] is None:
        return st
    st["built"] = True
    if not all(objs):
        st["lods"] = False       # falta el LOD1 o el LOD2: se generan al armar las UV
        return st
    ds = [uv_state(o.data) for o in objs]
    if all(d and not d["stale"] and d.get("mode") == st["mode"] for d in ds):
        st["uv"] = True
        st["info"] = ds[0].get("info")
        if st["mode"] == "BAKE":
            st["bake"] = all(bpy.data.images.get(ao_image_name(name, i)) is not None and
                             bpy.data.images[ao_image_name(name, i)].get("cs2_uid") == d.get("uid")
                             for i, d in enumerate(ds))
        a = objs[0].data.get("cs2_assigned")
        st["assigned"] = bool(a) and a == ds[0].get("uid") and (st["bake"] or st["mode"] == "REPEAT")
    elif any(ds):
        st["stale"] = True
    return st


def uv_generate(context, p, objs, progress=None):
    """Paso 1: UV de los tres LODs. Modo BAKE: islas únicas por importancia. Modo REPEAT: el atlas con repetición."""
    name = p.get("building_name", "Residencial01")
    sizes = bake_lod_sizes(int(p.get("tex_size", 1024)), p)
    infos = []
    shared = tex_shared(p)
    if shared:          # el LOD0 y el LOD1 arman sus islas juntas en el mismo mapa
        if progress:
            progress(0.1, "UV del LOD0 y LOD1 (un solo mapa)")
        infos = make_unique_uv_shared([ob.data for ob in objs[:2]], p, sizes[0])
    for lod, ob in enumerate(objs):
        me = ob.data
        if shared and lod < 2:
            me["cs2_assigned"] = ""
            continue
        if progress:
            progress(lod / 3.0, f"UV del LOD{lod}")
        uid = None
        if p.get("bake_ao"):
            info = make_unique_uv(me, p if lod == 0 else dict(p, uv_pins=""), sizes[min(lod, 2)])   # lo fijado vale para el LOD0
        else:
            if "CS2_unique" in me.uv_layers:
                me.uv_layers.remove(me.uv_layers["CS2_unique"])
            ext = float(ob.get("cs2_ext", ATLAS_SCALE))
            try:
                atl = json.loads(ob.get("cs2_atlas", "{}"))
            except ValueError:
                atl = {}
            used = sum(v[2] * v[3] for v in atl.values() if len(v) >= 4) if atl else 0.0
            info = {"caras": len(me.polygons), "uso": round(min(1.0, used) * 100.0, 1), "px_por_m": round(ext * sizes[min(lod, 2)], 1)}
            uid = "%08x" % random.getrandbits(32)
            me["cs2_uv"] = json.dumps({"mode": "REPEAT", "sig": _mesh_sig(me), "uid": uid, "info": info})
        me["cs2_assigned"] = ""          # las UV cambiaron: hay que asignar de nuevo
        infos.append(info)
    # las sombras viejas ya no valen
    for lod in range(3):
        img = bpy.data.images.get(ao_image_name(name, lod))
        if img is not None:
            bpy.data.images.remove(img)
    clear_texture_view(objs, p)
    if p.get("bake_ao"):
        for ob in objs:               # el editor UV muestra lo que se va a hornear (no el atlas de origen)
            if "CS2_unique" in ob.data.uv_layers:
                ob.data.uv_layers.active = ob.data.uv_layers["CS2_unique"]
    return infos


def bake_ao_all(context, p, objs, progress=None):
    """Paso 2: oclusión ambiental de cada LOD sobre sus UV únicas (lo lento)."""
    name = p.get("building_name", "Residencial01")
    sizes = bake_lod_sizes(int(p.get("tex_size", 1024)), p)
    for lod, ob in enumerate(objs):
        me = ob.data
        d = uv_state(me)
        if not d or d["stale"] or d.get("mode") != "BAKE":
            raise RuntimeError("Primero generá las UV (paso 1)")
        if progress:
            progress(lod / 3.0, f"Sombras del LOD{lod}")
        reps = me.get("cs2_reps")
        only = None
        if reps is not None and len(reps) == len(me.polygons):
            only = set(i for i, r in enumerate(reps) if i == r)
        me["cs2_bake_margin"] = int(p.get("bake_margin", 8))
        size = int(d["info"].get("tam", sizes[min(lod, 2)]))
        ao = compute_ao_map(me, "CS2_unique", size, float(p.get("bake_dist", 2.5)),
                            max(4, int(p.get("bake_samples", 32))), only=only)
        store_ao(name, lod, ao, d["uid"])
        me["cs2_assigned"] = ""


def _view_images(name, lod, tex):
    imgs = {}
    for key in ("BaseColor", "Normal", "MaskMap"):
        arr = tex[key]
        size = arr.shape[0]
        nm = f"CS2_view_{name}_LOD{lod}_{key}"
        img = bpy.data.images.get(nm)
        if img is not None and img.size[0] != size:
            bpy.data.images.remove(img)
            img = None
        if img is None:
            img = bpy.data.images.new(nm, size, size, alpha=True)
        img.colorspace_settings.name = "sRGB" if key == "BaseColor" else "Non-Color"
        img.alpha_mode = "CHANNEL_PACKED" if key == "MaskMap" else "STRAIGHT"
        img.pixels.foreach_set(arr.ravel())
        img.update()
        imgs[key] = img
    return imgs


def _orig_material(m):
    o = m.get("cs2_orig")
    return bpy.data.materials.get(o) if o else m


def clear_texture_view(objs, p=None):
    """Vuelve el modelo a colores lisos (sin texturas en el visor)."""
    for ob in objs:
        me = ob.data
        for i, m in enumerate(list(me.materials)):
            if m is not None and m.get("cs2_orig"):
                o = _orig_material(m)
                if o is not None:
                    me.materials[i] = o
        atlas = next((l for l in me.uv_layers if l.name != "CS2_unique"), None)
        if atlas is not None:
            atlas.active_render = True
            me.uv_layers.active = atlas
        me["cs2_assigned"] = ""
    for m in list(bpy.data.materials):
        if m.get("cs2_orig") and m.users == 0:
            bpy.data.materials.remove(m)
    q = dict(p or {})
    q["pbr_preview"] = False
    if objs:
        q.setdefault("building_name", objs[0].name.rsplit("_LOD", 1)[0])
        update_preview(q)


def assign_textures(context, p, objs, progress=None):
    """Paso 3: aplica los materiales (color y mapas PBR) a las UV y los muestra en el modelo."""
    name = p.get("building_name", "Residencial01")
    size = int(p.get("tex_size", 1024))
    if not p.get("bake_ao"):
        q = dict(p)
        q["pbr_preview"] = True
        update_preview(q)
        for ob in objs:
            d = uv_state(ob.data)
            ob.data["cs2_assigned"] = d["uid"] if d else ""
        return
    arrays = texture_arrays(p, size)
    shared_imgs = None
    if tex_shared(p):            # un solo juego de texturas para los tres LOD
        for lod, ob in enumerate(objs):
            d = uv_state(ob.data)
            if not d or d["stale"] or read_ao(name, lod, d["uid"]) is None:
                raise RuntimeError("Primero generá las UV y horneá las sombras (pasos 1 y 2)")
        if progress:
            progress(0.1, "Horneando la textura compartida")
        texs_all, ums = bake_all_lods(context, p, objs, arrays, name)
        for um in ums:
            bpy.data.meshes.remove(um)
        shared_imgs = _view_images(name, 0, texs_all[0])
        lod2_imgs = _view_images(name, 2, texs_all[1]) if len(texs_all) > 1 else None
    for lod, ob in enumerate(objs):
        me = ob.data
        d = uv_state(me)
        if shared_imgs is not None and lod >= 2:
            imgs = lod2_imgs
        elif shared_imgs is None:
            ao = read_ao(name, lod, d["uid"] if d else None)
            if not d or d["stale"] or ao is None:
                raise RuntimeError("Primero generá las UV y horneá las sombras (pasos 1 y 2)")
            if progress:
                progress(lod / 3.0, f"Texturas del LOD{lod}")
            tex, umesh = bake_lod(context, me, p, arrays, ao.shape[0], ao=ao, have_uv=True)
            bpy.data.meshes.remove(umesh)
            imgs = _view_images(name, lod, tex)
        else:
            imgs = shared_imgs
        me.uv_layers["CS2_unique"].active_render = True
        me.uv_layers.active = me.uv_layers["CS2_unique"]
        for i, m in enumerate(list(me.materials)):
            if m is None or i in SUB_MATS:
                continue
            o = _orig_material(m)
            if o is None:
                continue
            cn = f"{o.name}__v{lod}"
            c = bpy.data.materials.get(cn)
            if c is None:
                c = o.copy()
                c.name = cn
            c["cs2_orig"] = o.name
            _preview_nodes(c, imgs)
            me.materials[i] = c
        me["cs2_assigned"] = d["uid"]
    for m in list(bpy.data.materials):
        if m.get("cs2_orig") and m.users == 0:
            bpy.data.materials.remove(m)


def ensure_textures(context, p, objs, a=0.0, b=0.5):
    """Completa lo que falte (UV y sombras) antes de exportar. Devuelve la lista de pasos que hizo.
    Si hay una barra de progreso activa, usa el tramo [a, b] de ella."""
    name = p.get("building_name", "Residencial01")
    done = []
    mode = "BAKE" if p.get("bake_ao") else "REPEAT"
    ds = [uv_state(o.data) for o in objs]
    if not all(d and not d["stale"] and d.get("mode") == mode for d in ds):
        uv_generate(context, p, objs, _sub_cb(context, a, a + (b - a) * (0.4 if mode == "BAKE" else 1.0)))
        done.append("UV")
    if mode == "BAKE":
        ds = [uv_state(o.data) for o in objs]
        if not all(read_ao(name, i, d["uid"]) is not None for i, d in enumerate(ds)):
            bake_ao_all(context, p, objs, _sub_cb(context, a + (b - a) * 0.4, b))
            done.append("sombras")
    return done



def bake_lod(context, src_mesh, p, arrays, size, ao=None, have_uv=False):
    """Transfiere las texturas de un LOD a sus UV únicas (capa 'CS2_unique', que se crea si falta) y les aplica la
    oclusión `ao` (si falta, se calcula). Devuelve (texturas, malla con UV únicas).

    Las caras de ventana/vidrio conservan sus UV. La malla devuelta es nueva (la libera quien llama)."""
    import numpy as np
    sc = context.scene
    keep_engine, keep_samples = sc.render.engine, None
    bk = sc.render.bake
    keep_bake = (bk.margin, bk.margin_type, bk.use_clear)
    me = src_mesh.copy()
    me.name = "cs2_bake_mesh"
    if not have_uv or "CS2_unique" not in me.uv_layers:
        make_unique_uv(me, p, size)
    base_uv = next(l for l in me.uv_layers if l.name != "CS2_unique")
    base_name = base_uv.name
    n = len(base_uv.data)
    ob = bpy.data.objects.new("cs2_bake_tmp", me)
    sc.collection.objects.link(ob)
    world_tmp = None
    imgs, mat = [], None
    try:
        for v in list(context.view_layer.objects):
            if v is not None:
                try:
                    v.select_set(False)
                except RuntimeError:
                    pass
        ob.select_set(True)
        context.view_layer.objects.active = ob
        for poly in me.polygons:
            poly.select = poly.material_index not in SUB_MATS
        me.uv_layers.active = me.uv_layers["CS2_unique"]
        me = ob.data
        me.uv_layers.active = me.uv_layers["CS2_unique"]
        base_uv = me.uv_layers[base_name]
        # ventanas y vidrio no se hornean: sus UV únicas se sacan del 0-1 mientras dura el horneo
        uni = me.uv_layers["CS2_unique"]
        fixed = []
        for poly in me.polygons:
            if poly.material_index in SUB_MATS:
                fixed += list(poly.loop_indices)
        if fixed:
            uv_all = np.empty(n * 2, np.float32)
            uni.data.foreach_get("uv", uv_all)
            uv2 = uv_all.reshape(-1, 2)
            uv2[fixed] = (-4.0, -4.0)
            uni.data.foreach_set("uv", uv2.ravel())

        if ao is None:    # oclusión ambiental: rayos propios sobre las UV únicas (no depende de Cycles)
            me["cs2_bake_margin"] = int(p.get("bake_margin", 8))
            reps = me.get("cs2_reps")
            only = set(i for i, r in enumerate(reps) if i == r) if reps is not None and len(reps) == len(me.polygons) else None
            ao = compute_ao_map(me, "CS2_unique", size, float(p.get("bake_dist", 2.5)),
                                max(4, int(p.get("bake_samples", 32))), only=only)
        size = ao.shape[0]

        # --- material de horneo: una sola ranura; se restauran las ranuras y los índices al terminar
        idx = np.empty(len(me.polygons), np.int32)
        me.polygons.foreach_get("material_index", idx)
        old_mats = list(me.materials)
        me.polygons.foreach_set("material_index", np.zeros(len(idx), np.int32))
        me.materials.clear()
        mat = bpy.data.materials.new("cs2_bake_mat")
        mat.use_nodes = True
        nt = mat.node_tree
        for nd in list(nt.nodes):
            nt.nodes.remove(nd)
        out = nt.nodes.new("ShaderNodeOutputMaterial")
        emi = nt.nodes.new("ShaderNodeEmission")
        pri = nt.nodes.new("ShaderNodeBsdfPrincipled")
        uvn = nt.nodes.new("ShaderNodeUVMap")
        uvn.uv_map = base_name
        src = nt.nodes.new("ShaderNodeTexImage")
        src.interpolation = "Linear"
        nmap = nt.nodes.new("ShaderNodeNormalMap")
        nmap.uv_map = base_name
        tgt = nt.nodes.new("ShaderNodeTexImage")
        nt.links.new(uvn.outputs["UV"], src.inputs["Vector"])
        nt.links.new(emi.outputs["Emission"], out.inputs["Surface"])
        me.materials.append(mat)

        sc.render.engine = "CYCLES"
        sc.cycles.device = "CPU"
        keep_samples = sc.cycles.samples
        bk.margin, bk.margin_type, bk.use_clear = bake_dilate_px(p, size), "EXTEND", True
        if sc.world is None:
            world_tmp = bpy.data.worlds.new("cs2_bake_world")
            sc.world = world_tmp
        keep_dist = sc.world.light_settings.distance
        sc.world.light_settings.distance = float(p.get("bake_dist", 2.5))
        asz = arrays["BaseColor"].shape[0]

        def target(name):
            img = bpy.data.images.new(name, size, size, alpha=True)
            img.colorspace_settings.name = "Non-Color"
            imgs.append(img)
            tgt.image = img
            nt.nodes.active = tgt
            return img

        def read(img):
            a = np.empty(size * size * 4, np.float32)
            img.pixels.foreach_get(a)
            return a.reshape(size, size, 4)

        def emit(key, channel):      # channel: None = RGB, "A" = canal alfa como gris
            sim = _np_image(f"cs2_bsrc_{key}", arrays[key], asz)
            imgs.append(sim)
            src.image = sim
            for l in list(emi.inputs["Color"].links):
                nt.links.remove(l)
            nt.links.new(src.outputs["Alpha" if channel == "A" else "Color"], emi.inputs["Color"])
            for l in list(out.inputs["Surface"].links):
                nt.links.remove(l)
            nt.links.new(emi.outputs["Emission"], out.inputs["Surface"])
            sc.cycles.samples = 4
            t = target(f"cs2_bake_{key}{channel or ''}")
            bpy.ops.object.bake(type="EMIT")
            return read(t)

        res = {}
        base = emit("BaseColor", None)
        k = float(p.get("bake_ao_str", 0.8))
        base[..., :3] *= (1.0 - k + k * np.maximum(ao, 0.15))[..., None]    # piso: ni un rincón queda negro puro
        base[..., 3] = 1.0
        res["BaseColor"] = base
        # normal (tangente, OpenGL) con el relieve de la textura
        nim = _np_image("cs2_bsrc_Normal", arrays["Normal"], asz)
        imgs.append(nim)
        src.image = nim
        for l in list(nmap.inputs["Color"].links):
            nt.links.remove(l)
        nt.links.new(src.outputs["Color"], nmap.inputs["Color"])
        nt.links.new(nmap.outputs["Normal"], pri.inputs["Normal"])
        for l in list(out.inputs["Surface"].links):
            nt.links.remove(l)
        nt.links.new(pri.outputs["BSDF"], out.inputs["Surface"])
        sc.cycles.samples = 4
        tn = target("cs2_bake_normal")
        bpy.ops.object.bake(type="NORMAL", normal_space="TANGENT")
        nrm = read(tn)
        nrm[..., 3] = 1.0
        res["Normal"] = nrm
        for l in list(out.inputs["Surface"].links):
            nt.links.remove(l)
        nt.links.new(emi.outputs["Emission"], out.inputs["Surface"])
        mk = emit("MaskMap", None)
        mk[..., 3] = emit("MaskMap", "A")[..., 0]
        res["MaskMap"] = mk
        cm = emit("ControlMask", None)
        cm[..., 3] = emit("ControlMask", "A")[..., 0]
        res["ControlMask"] = cm
        if float(arrays["Emissive"][..., :3].max()) > 0.0:
            em = emit("Emissive", None)
            em[..., 3] = 1.0
        else:
            em = np.zeros((size, size, 4), np.float32)
            em[..., 3] = 1.0
        res["Emissive"] = em
        sc.world.light_settings.distance = keep_dist

        # --- restaurar ranuras e índices; la malla final lleva las UV únicas como UV0
        me.materials.clear()
        for m_ in old_mats:
            me.materials.append(m_)
        me.polygons.foreach_set("material_index", idx)
        if fixed:
            back = np.empty(n * 2, np.float32)
            me.uv_layers[base_name].data.foreach_get("uv", back)
            now = np.empty(n * 2, np.float32)
            me.uv_layers["CS2_unique"].data.foreach_get("uv", now)
            now = now.reshape(-1, 2)
            now[fixed] = back.reshape(-1, 2)[fixed]
            me.uv_layers["CS2_unique"].data.foreach_set("uv", now.ravel())
        base_uv = me.uv_layers[base_name]
        me.uv_layers.remove(base_uv)
        me.uv_layers[0].name = "UVMap"
        out_mesh = me
        me = None
        return {k_: res[k_] for k_ in TEX_SUFFIXES}, out_mesh
    finally:
        sc.render.engine = keep_engine
        if keep_samples is not None:
            sc.cycles.samples = keep_samples
        bk.margin, bk.margin_type, bk.use_clear = keep_bake
        if ob.name in bpy.data.objects:
            bpy.data.objects.remove(ob, do_unlink=True)
        if me is not None:
            bpy.data.meshes.remove(me)
        for im in imgs:
            bpy.data.images.remove(im)
        if mat is not None:
            bpy.data.materials.remove(mat)
        if world_tmp is not None:
            sc.world = None
            bpy.data.worlds.remove(world_tmp)


def bake_lod_sizes(size, p=None):
    """Tamaño de textura de cada LOD. Si el LOD0 y el LOD1 comparten texturas, usan el mismo tamaño; el LOD2 siempre el suyo."""
    if p is not None and tex_shared(p):
        return [size, size, max(512, size // 4)]
    return [size, min(size, 1024), max(512, size // 4)]


def _part_mesh(src_mesh, name, keep, single_mat=None):
    """Copia de la malla con solo las caras que cumplen `keep(material_index)`; None si queda vacía."""
    bm = bmesh.new()
    bm.from_mesh(src_mesh)
    drop = [f for f in bm.faces if not keep(f.material_index)]
    if drop:
        bmesh.ops.delete(bm, geom=drop, context="FACES")
    if not bm.faces:
        bm.free()
        return None
    for f in bm.faces:
        f.material_index = 0
    me = bpy.data.meshes.new(name)
    bm.to_mesh(me)
    bm.free()
    if single_mat is not None:
        me.materials.append(single_mat)   # la malla principal lleva un único material
    return me


def _view_dirs():
    out = []
    # direcciones densas: una pared detrás de una baranda dentro de un hueco profundo solo se ve en un rango angular angosto
    for el, n in ((0, 12), (12, 12), (24, 12), (36, 12), (50, 10), (65, 8), (80, 5), (-15, 10), (-30, 8), (-50, 6)):
        for k in range(n):
            a = 2 * math.pi * (k + (0.5 if (el // 12) % 2 else 0.0)) / n
            c = math.cos(math.radians(el))
            out.append(Vector((math.cos(a) * c, math.sin(a) * c, math.sin(math.radians(el)))))
    out.append(Vector((0, 0, 1)))
    return out


_VIEW_DIRS = None


SHELL_MATS = (MAT["facade"], MAT["facb"], MAT["facc"], MAT["facd"], MAT["glass"], MAT["gls"], MAT["door"], MAT["gar"])
TINY_FACE_AREA = 0.0015     # m²: caras de molduras o barandas más chicas que esto se borran al optimizar (≈ 4 × 4 cm)
TINY_FACE_EDGE = 0.07       # m: y además ninguno de sus lados pasa de esto


def clean_hidden_faces(mesh):
    """Borra las caras que ninguna cámara exterior puede ver (tapadas por otras partes o mirando al suelo).
    Devuelve (borradas, total)."""
    global _VIEW_DIRS
    from mathutils.bvhtree import BVHTree
    if _VIEW_DIRS is None:
        _VIEW_DIRS = _view_dirs()
    bm = bmesh.new()
    bm.from_mesh(mesh)
    bm.faces.ensure_lookup_table()
    total = len(bm.faces)
    if total == 0:
        bm.free()
        return 0, 0
    bvh = BVHTree.FromBMesh(bm, epsilon=1e-5)
    dead = []
    for f in bm.faces:
        n = f.normal
        c = f.calc_center_median()
        if n.z < -0.9 and c.z < 0.05:     # la cara de abajo apoyada en el suelo nunca se ve
            dead.append(f)
            continue
        vs = [v.co for v in f.verts]
        area = f.calc_area()
        if area < TINY_FACE_AREA and f.material_index in (MAT["trim"], MAT["rail"]) and \
                max((vs[i] - vs[i - 1]).length for i in range(len(vs))) < TINY_FACE_EDGE:
            dead.append(f)      # tapas de barras y cantos de molduras de ~3 cm: a 70 px/m ocupan 2 px y solo suman triángulos
            continue
        if abs(n.z) < 0.7 and (f.material_index in SHELL_MATS or (f.material_index == MAT["trim"] and area > 0.05)):
            continue      # pared, vidrio y puertas verticales: son la cáscara. Aunque casi no se vean (tras persianas, bajo una
            #               cornisa), borrarlas abre un hueco por el que se ve el interior; lo tapado de verdad es poco y no vale el riesgo
        if area > 2.0 and len(vs) != 4:   # paredes grandes con huecos o formas raras: no se pueden probar bien, se dejan
            continue
        if area > 2.0:   # pared grande tapada en parte por ventanas o balcones pegados: grilla de puntos
            k = 8
            pts = [vs[0].lerp(vs[1], (i + 0.5) / k).lerp(vs[3].lerp(vs[2], (i + 0.5) / k), (j + 0.5) / k)
                   for i in range(k) for j in range(k)]
        else:
            pts = [c]
        if area > 2.0:
            pass
        elif area > 0.15:   # varios puntos: una cara parcialmente tapada sigue siendo visible
            pts += [c + (v - c) * 0.8 for v in vs]
            pts += [c + ((vs[i] + vs[(i + 1) % len(vs)]) / 2 - c) * 0.8 for i in range(len(vs))]
            if len(vs) == 4 and f.material_index in (MAT["facade"], MAT["facb"], MAT["facc"], MAT["facd"]):
                # una franja de pared bajo una cornisa o sobre una moldura solo se ve por una rendija: con pocos puntos
                # todos caen en la sombra y se borraba (quedaba un hueco). Una grilla fina la encuentra.
                k = 5
                pts += [vs[0].lerp(vs[1], (i + 0.5) / k).lerp(vs[3].lerp(vs[2], (i + 0.5) / k), (j + 0.5) / k)
                        for i in range(k) for j in range(k)]
        else:                      # caras chicas/largas (barrotes): también a lo largo de su lado mayor
            e = max(((vs[i + 1 - len(vs)] - vs[i]) for i in range(len(vs))), key=lambda v: v.length)
            pts += [c + e * 0.3, c - e * 0.3]
        seen = False
        for q in pts:
            o = q + n * 0.003
            for d in _VIEW_DIRS:
                if d.dot(n) < 0.12:
                    continue
                if bvh.ray_cast(o, d, 1e4)[0] is None:
                    seen = True
                    break
            if seen:
                break
        if not seen:
            dead.append(f)
    if dead:
        bmesh.ops.delete(bm, geom=dead, context="FACES")
    bm.to_mesh(mesh)
    bm.free()
    mesh.update()
    mesh["cs2_clean"] = True      # marca: ya está limpia (se pierde al volver a generar)
    return len(dead), total


def export_building(context, objs, name, out, theme="NA", level=1, size=(16.0, 16.0), p=None, clean=False):
    """Un FBX por malla en <out>/ProjectFiles/<asset>/ (como pide el importador del juego).

    Principal: <asset>, <asset>_LOD1, <asset>_LOD2, cada una con UN material del mismo nombre.
    Submallas sin material: <...>_Win (ventanas) y <...>_Gls (vidrio suelto; no hay en LOD2).
    Texturas: <asset>_BaseColor/ControlMask/MaskMap/Normal/Emissive.png (el LOD2 comparte el atlas).
    """
    if p is None:
        p = props_to_params(context.scene.cs2_building)
    asset = asset_export_name(theme, level, name, size[0], size[1])
    out = os.path.join(out, "ProjectFiles", asset)
    os.makedirs(out, exist_ok=True)
    bake = bool(p.get("bake_ao"))
    arrays = None
    _prog_step(context, 0.02, "Preparando la exportación")
    if bake:
        ensure_textures(context, dict(p, building_name=name), list(objs), 0.02, 0.5)   # UV y sombras que falten
        _prog_step(context, 0.5, "Armando las texturas")
        arrays = texture_arrays(p, int(p.get("tex_size", 1024)))
        sizes = bake_lod_sizes(int(p.get("tex_size", 1024)), p)
        if not tex_shared(p):
            try:
                os.remove(os.path.join(out, "settings.json"))
            except OSError:
                pass
    else:
        if not (p.get("tex_share_asset") or "").strip():
            write_textures(out, asset, p, int(p.get("tex_size", 1024)))
        write_shared_settings(out, asset, p, [i for i in range(1, len(objs))])
    made = []
    coll = context.scene.collection
    shared = bake and tex_shared(p)
    sh_tex, sh_meshes = [], []
    try:
        for lod, src in enumerate(objs):
            _prog_step(context, 0.55 + 0.43 * lod / max(1, len(objs)), f"Exportando LOD{lod}")
            base = asset if lod == 0 else f"{asset}_LOD{lod}"
            real_me = src.data
            mat = bpy.data.materials.new(base)
            mat.use_nodes = True
            made.append(("mat", mat))
            if clean and not src.data.get("cs2_clean"):  # sobre una copia: la malla del edificio no se toca
                tmp = src.data.copy()
                made.append(("mesh", tmp))
                clean_hidden_faces(tmp)
                src = type("S", (), {"data": tmp})()
            if bake:   # UV únicas + oclusión: cada LOD con sus propias texturas
                d_ = uv_state(real_me)
                ao_ = read_ao(name, lod, d_["uid"] if d_ else None)
                tex, umesh = bake_lod(context, src.data, p, arrays, sizes[min(lod, 2)], ao=ao_, have_uv=True)
                made.append(("mesh", umesh))
                src = type("S", (), {"data": umesh})()
                if shared and lod < 2:      # el LOD0 y el LOD1 comparten un juego de texturas: se une y se escribe después
                    sh_tex.append(tex)
                    sh_meshes.append(umesh)
                else:
                    write_arrays(out, base, tex, sizes[min(lod, 2)])
            parts = [(base, _part_mesh(src.data, base, lambda i: i not in SUB_MATS, mat)),
                     (f"{base}_Win", _part_mesh(src.data, f"{base}_Win", lambda i: i == MAT["glass"]))]
            if lod < 2:  # en LOD2 el vidrio se quita (el tinte va en la textura)
                parts.append((f"{base}_Gls", _part_mesh(src.data, f"{base}_Gls", lambda i: i == MAT["gls"])))
            for pname, me in parts:
                if me is None:
                    continue
                old = bpy.data.objects.get(pname)
                if old is not None and old.data is not me:  # por si quedó algo de un intento anterior
                    bpy.data.objects.remove(old, do_unlink=True)
                ob = bpy.data.objects.new(pname, me)
                coll.objects.link(ob)   # todas en el origen, sin rotación: misma posición que su malla madre
                made.append(("obj", ob))
                made.append(("mesh", me))
        if shared:
            _prog_step(context, 0.96, "Uniendo la textura compartida")
            comp = composite_lod_textures(sh_meshes, sh_tex, sizes[0], p)
            write_arrays(out, asset, comp, sizes[0])
            for n_ in (1,):      # restos de una exportación anterior con texturas propias del LOD1
                for suf in TEX_SUFFIXES:
                    try:
                        os.remove(os.path.join(out, f"{asset}_LOD{n_}_{suf}.png"))
                    except OSError:
                        pass
            write_shared_settings(out, asset, dict(p, tex_share_asset=""), [1])       # solo el LOD1 comparte; el LOD2 lleva las suyas
        context.view_layer.update()
        for kind, ob in made:
            if kind == "obj":
                export_fbx(os.path.join(out, f"{ob.name}.fbx"), [ob])
    finally:
        for kind, it in made:
            try:
                if kind == "obj":
                    bpy.data.objects.remove(it, do_unlink=True)
            except ReferenceError:
                pass
        for kind, it in made:
            try:
                if kind == "mesh":
                    bpy.data.meshes.remove(it)
                elif kind == "mat":
                    bpy.data.materials.remove(it)
            except ReferenceError:
                pass
    _prog_step(context, 0.99, "Terminado")
    return asset


# ----------------------------------------------------------------------------
# Exportación a Cities: Skylines 1 (wiki cslmodding.info/asset/building)
# ----------------------------------------------------------------------------
CS1_MAX_VERTS = 65536
CS1_OTHER_LOTS = ((16, 8), (15, 9), (14, 11), (13, 12), (12, 13), (11, 14), (9, 15), (8, 16))


def cs1_name(name):
    """Nombre de malla para CS1: sin espacios ni guiones bajos (el sufijo _lod lo agrega el juego)."""
    import re
    return re.sub(r"[^A-Za-z0-9]", "", name or "") or "Edificio"


def cs1_lot_text(w, d, zoned):
    """Advertencia de lote para CS1 (None si entra). Zonificado: hasta 4x4 celdas."""
    cx, cz = lot_cells(w, d)
    if zoned:
        if cx > 4 or cz > 4:
            return f"Lote {cx}x{cz} celdas: en CS1 un edificio zonificado llega a 4x4"
        return None
    if not any(cx <= a and cz <= b for a, b in CS1_OTHER_LOTS):
        return f"Lote {cx}x{cz} celdas: en CS1 el máximo es 16x8 (o 8x16, 15x9, 14x11, 13x12...)"
    return None


def _cs1_gray(v):
    import numpy as np
    a = np.ones(v.shape + (4,), np.float32)
    a[..., :3] = v[..., None]
    return a


def cs1_maps(arrays, scale_d=True):
    """Mapas de CS1 (_d, _n, _s y, si hay, _c) a partir de las texturas del edificio. El difuso se escala para que
    lo más claro quede bajo ~140/255 (pide la wiki), manteniendo el contraste y los colores."""
    import numpy as np
    base = arrays["BaseColor"].copy()
    if scale_d:
        lum = base[..., :3].mean(axis=2)
        top = float(np.percentile(lum, 99.5))
        f = min(1.0, 0.55 / max(top, 1e-3))
        base[..., :3] *= f
    base[..., 3] = 1.0
    nrm = arrays["Normal"].copy()
    nrm[..., 3] = 1.0
    g = arrays["MaskMap"][..., 3]
    spec = np.where(g < 0.5, g * 0.5, g)      # paredes y techos opacos (<80%); el vidrio queda sobre 205/255
    out = {"d": base, "n": nrm, "s": _cs1_gray(spec)}
    cm = arrays["ControlMask"][..., :3].max(axis=2)
    if float(cm.max()) > 0.0:
        out["c"] = _cs1_gray(cm)
    return out


CS1_TILE_VARIANTS = 3
CS1_SLOT = {"win": len(MAT_ORDER), "door": len(MAT_ORDER) + 1, "gar": len(MAT_ORDER) + 2,
            "gls": len(MAT_ORDER) + 3, "rail": len(MAT_ORDER) + 4}
CS1_NIGHT = (140, 190, 250)      # valores de iluminación de las variantes de ventana (128-255 = se prenden al azar)


def _cs1_kind(mi):
    if mi == MAT["glass"]:
        return "win"
    if mi == MAT["gls"]:
        return "gls"
    if mi == MAT["door"]:
        return "door"
    if mi == MAT["gar"]:
        return "gar"
    if mi == MAT["rail"]:
        return "rail"
    return None


def _cs1_face_info(mi, coords, nrm):
    """(tipo, ancho, alto, tangente, a_min, z_min) de una cara vertical que lleva imagen propia, o None."""
    k = _cs1_kind(mi)
    if k is None or abs(nrm.z) > 0.3:
        return None
    t = Vector((0, 0, 1)).cross(nrm)
    if t.length < 1e-6:
        return None
    t.normalize()
    a = [c.dot(t) for c in coords]
    z = [c.z for c in coords]
    w, h = max(a) - min(a), max(z) - min(z)
    if k in ("rail", "gls") and (w < 0.25 or h < 0.25):
        return None       # tiras finas (pasamanos, parantes): quedan con la imagen de su material
    return k, w, h, t, min(a), min(z)


def _cs1_key(kind, w, h):
    return kind, max(4, int(round(w / 0.05))), max(4, int(round(h / 0.05)))


def cs1_openings(me):
    """Tamaños distintos de ventana, puerta, portón, vidrio y reja de una malla: {(tipo, ancho, alto): cantidad}."""
    out = {}
    for pl in me.polygons:
        d = _cs1_face_info(pl.material_index, [me.vertices[me.loops[li].vertex_index].co for li in pl.loop_indices], pl.normal)
        if d is None:
            continue
        key = _cs1_key(d[0], d[1], d[2])
        out[key] = out.get(key, 0) + 1
    return out


def cs1_layout(atlas, size, ppm, keys):
    """Reparte, en el espacio libre del atlas, un recuadro de imagen por cada tamaño de abertura
    (las ventanas con 3 variantes de iluminación). Devuelve {size, ppm, tiles:[{k,wq,hq,v,x,y,W,H}]}."""
    import numpy as np
    occ = np.zeros((size, size), bool)
    for (a0, b0, a1, b1) in atlas.values():
        occ[max(0, int(b0 * size) - 3):int(b1 * size) + 4, max(0, int(a0 * size) - 3):int(a1 * size) + 4] = True
    want = []
    for (k, wq, hq) in sorted(keys, key=lambda t: -t[1] * t[2]):
        for v in range(CS1_TILE_VARIANTS if k == "win" else 1):
            want.append((k, wq, hq, v))
    tiles, lost = [], 0
    for (k, wq, hq, v) in want:
        sc = 1.0
        for _try in range(5):
            W = int(min(size // 3, max(10, math.ceil(wq * 0.05 * ppm * sc))))
            H = int(min(size // 3, max(10, math.ceil(hq * 0.05 * ppm * sc))))
            ii = np.zeros((size + 1, size + 1), np.int32)
            ii[1:, 1:] = occ.cumsum(0).cumsum(1)
            ww, hh = W + 4, H + 4
            ny, nx = size - hh + 1, size - ww + 1
            if ny <= 0 or nx <= 0:
                sc *= 0.7
                continue
            tot = ii[hh:hh + ny, ww:ww + nx] - ii[0:ny, ww:ww + nx] - ii[hh:hh + ny, 0:nx] + ii[0:ny, 0:nx]
            free = np.argwhere(tot[::2, ::2] == 0)
            if len(free):
                y, x = free[0] * 2
                occ[y:y + hh, x:x + ww] = True
                tiles.append({"k": k, "wq": wq, "hq": hq, "v": v, "x": int(x + 2), "y": int(y + 2), "W": W, "H": H})
                break
            sc *= 0.7
        else:
            lost += 1
    return {"size": size, "ppm": ppm, "tiles": tiles, "lost": lost}


def cs1_assign_uv(me, layout):
    """Lleva las ventanas, puertas y portones de la malla a su recuadro de imagen y les pone su ranura de material."""
    size = layout["size"]
    idx = {(t["k"], t["wq"], t["hq"], t["v"]): t for t in layout["tiles"]}
    bm = bmesh.new()
    bm.from_mesh(me)
    layers = list(bm.loops.layers.uv)
    uvl = next((l for l in layers if l.name != "CS2_unique"), layers[0])
    n_ok = 0
    for f in bm.faces:
        d = _cs1_face_info(f.material_index, [v.co for v in f.verts], f.normal)
        if d is None:
            continue
        k, w, h, t, a0, z0 = d
        key = _cs1_key(k, w, h)
        nv = CS1_TILE_VARIANTS if k == "win" else 1
        c = f.calc_center_median()
        v0 = int(_hq("wv", round(c.x, 1), round(c.y, 1), round(c.z, 1)) * nv) % nv
        tl = idx.get(key + (v0,)) or next((idx[key + (j,)] for j in range(nv) if key + (j,) in idx), None)
        if tl is None:
            continue
        for lp in f.loops:
            a, z = lp.vert.co.dot(t), lp.vert.co.z
            uu = 0.0 if w < 1e-6 else min(1.0, max(0.0, (a - a0) / w))
            vv = 0.0 if h < 1e-6 else min(1.0, max(0.0, (z - z0) / h))
            lp[uvl].uv = ((tl["x"] + 0.5 + uu * (tl["W"] - 1)) / size, (tl["y"] + 0.5 + vv * (tl["H"] - 1)) / size)
        f.material_index = CS1_SLOT[k]
        n_ok += 1
    bm.to_mesh(me)
    bm.free()
    return n_ok


def _cs1_h2n(hm, strength=2.5):
    """Normal (OpenGL, +Y arriba) a partir de un mapa de alturas suavizado."""
    import numpy as np
    pad = np.pad(hm, 1, mode="edge")
    hb = (pad[:-2, 1:-1] + pad[2:, 1:-1] + pad[1:-1, :-2] + pad[1:-1, 2:] + 4 * pad[1:-1, 1:-1]) / 8.0
    gy, gx = np.gradient(hb)
    nn = np.dstack([-gx * strength, -gy * strength, np.ones_like(hb)])
    nn /= np.linalg.norm(nn, axis=2, keepdims=True)
    return nn * 0.5 + 0.5


def _cs1_region(arrays, atlas, size, name):
    """Recorte del atlas del edificio (color, normal, brillo) de un material, o None si no se usa."""
    r = atlas.get(name)
    if not r:
        return None
    x0, x1, y0, y1 = int(r[0] * size), int(r[2] * size), int(r[1] * size), int(r[3] * size)
    if x1 - x0 < 4 or y1 - y0 < 4:
        return None
    return {"base": arrays["BaseColor"][y0:y1, x0:x1, :3], "nrm": arrays["Normal"][y0:y1, x0:x1, :3],
            "gl": arrays["MaskMap"][y0:y1, x0:x1, 3]}


def _cs1_sample(reg, flat, W, H, ratio):
    """Material de una imagen de abertura: el recorte del atlas a la misma escala (respeta la textura del material)
    o, si el material no se usa, su color liso."""
    import numpy as np
    if reg is None:
        b = np.empty((H, W, 3), np.float32)
        b[:] = flat
        n = np.empty((H, W, 3), np.float32)
        n[:] = (0.5, 0.5, 1.0)
        return {"base": b, "nrm": n, "gl": np.full((H, W), 0.2, np.float32)}
    rh, rw = reg["gl"].shape
    ix = (np.arange(W) * ratio).astype(int) % rw
    iy = (np.arange(H) * ratio).astype(int) % rh
    return {"base": reg["base"][iy][:, ix], "nrm": reg["nrm"][iy][:, ix], "gl": reg["gl"][iy][:, ix]}


def _cs1_tile(kind, w, h, W, H, fr, lf, glass_col, var):
    """Imagen de una abertura: (color, normal, brillo, iluminación). Marco, vidrio, parantes y relieve; el marco
    toma el material de las molduras y la hoja el de la puerta, el portón o la reja."""
    import numpy as np
    X, Y = np.meshgrid((np.arange(W) + 0.5) * w / W, (np.arange(H) + 0.5) * h / H)
    fw = min(0.07, w * 0.14, h * 0.14)
    illum = np.zeros((H, W), np.float32)
    gcol = np.array(glass_col, np.float32)
    shade = (0.78 + 0.3 * (Y / max(h, 0.1)) + 0.12 * np.clip(1.0 - np.abs((X + Y * 0.6) % 1.2 - 0.6) * 4, 0, 1))[..., None]
    glassimg = np.clip(gcol * shade, 0, 1)
    if kind == "win":
        inner = (X > fw) & (X < w - fw) & (Y > fw * 1.7) & (Y < h - fw)
        bar = np.zeros_like(inner)
        if w >= 1.0:
            bar |= np.abs(X - w / 2) < 0.022
        if h >= 1.5:
            bar |= np.abs(Y - h * 0.72) < 0.022
        glass = inner & ~bar
        hm = np.where(glass, 0.2, 1.0).astype(np.float32)
        base = np.where(glass[..., None], glassimg, fr["base"])
        nsrc = fr["nrm"]
        gloss = np.where(glass, 0.92, fr["gl"]).astype(np.float32)
        illum[glass] = CS1_NIGHT[var % 3] / 255.0
    elif kind == "gls":
        hm = np.full((H, W), 0.5, np.float32)
        base, nsrc, gloss = glassimg, np.broadcast_to(np.array((0.5, 0.5, 1.0), np.float32), (H, W, 3)), np.full((H, W), 0.92, np.float32)
    elif kind == "door":
        leaf = (X > fw) & (X < w - fw) & (Y > 0.0) & (Y < h - fw)
        hm = np.where(leaf, 0.6, 1.0).astype(np.float32)
        base = np.where(leaf[..., None], lf["base"], fr["base"]).astype(np.float32)
        nsrc = np.where(leaf[..., None], lf["nrm"], fr["nrm"])
        gloss = np.where(leaf, np.maximum(lf["gl"], 0.3), fr["gl"]).astype(np.float32)
        if w > 0.7 and h > 1.4:       # dos cuarterones hundidos
            px, py = max(0.12, w * 0.18), 0.15
            for y0, y1 in ((py, h * 0.45), (h * 0.5, h - py - fw)):
                pan = (X > px) & (X < w - px) & (Y > y0) & (Y < y1)
                hm[pan] = 0.35
                base[pan] = base[pan] * 0.85
        hdl = ((X - (w - fw - 0.12)) ** 2 + (Y - h * 0.45) ** 2) < 0.035 ** 2
        base[hdl] = (0.8, 0.75, 0.55)
        hm[hdl] = 1.0
        gloss[hdl] = 0.6
    elif kind == "gar":   # portón seccional
        leaf = (X > fw * 0.6) & (X < w - fw * 0.6) & (Y > 0.0) & (Y < h - fw * 0.6)
        hm = np.where(leaf, 0.6, 1.0).astype(np.float32)
        base = np.where(leaf[..., None], lf["base"], fr["base"]).astype(np.float32)
        nsrc = np.where(leaf[..., None], lf["nrm"], fr["nrm"])
        gloss = np.where(leaf, np.maximum(lf["gl"], 0.35), fr["gl"]).astype(np.float32)
        ns = max(2, int(round(h / 0.55)))
        sy = (Y / max(h, 0.1)) * ns
        groove = leaf & (np.abs(sy - np.round(sy)) < 0.08 * ns / max(1.0, h))
        hm[groove] = 0.1
        base[groove] = base[groove] * 0.6
    else:   # reja o baranda de metal: barrotes sobre fondo oscuro, con pasamanos arriba y abajo
        cap = (Y > h - 0.06) | (Y < 0.05)
        gap = max(0.09, min(0.14, w / 8))
        bars = (np.abs(((X / gap) % 1.0) - 0.5) < 0.12) | cap | (X < 0.03) | (X > w - 0.03)
        hm = np.where(bars, 1.0, 0.05).astype(np.float32)
        base = np.where(bars[..., None], lf["base"], lf["base"] * 0.3).astype(np.float32)
        nsrc = lf["nrm"]
        gloss = np.where(bars, np.maximum(lf["gl"], 0.35), 0.1).astype(np.float32)
    nt = _cs1_h2n(hm) * 2.0 - 1.0
    nn = nt + (np.asarray(nsrc) * 2.0 - 1.0) - np.array((0.0, 0.0, 1.0))
    nn /= np.maximum(np.linalg.norm(nn, axis=2, keepdims=True), 1e-6)
    return base, nn * 0.5 + 0.5, gloss, illum


def cs1_paint(arrays, layout, p, atlas=None, ext=None):
    """Copia de las texturas del edificio con las imágenes de ventanas, puertas, portones, vidrio y rejas pintadas en
    su lugar (usando los materiales del edificio). Devuelve (texturas, mapa de iluminación 0-1)."""
    import numpy as np
    out = {k: v.copy() for k, v in arrays.items()}
    size = layout["size"]
    if atlas is None:
        atlas, ext = bake_context(p)
    illum = np.zeros((size, size), np.float32)
    mc = dict(MAT_COLORS)
    mc.update(p.get("colors") or {})
    flat = {"trim": _srgb(mc["trim"]), "door": _srgb(mc["door"]), "gar": _srgb(mc["gar"]), "rail": _srgb(mc["rail"])}
    regs = {nm: _cs1_region(arrays, atlas, size, nm) for nm in ("trim", "door", "gar", "rail", "gls", "facade", "facb", "facc", "facd")}
    frm = SURF_MATS.get(int((p.get("look") or {}).get("frm_mat", 1)), "trim")
    flat["facade"] = _srgb(p["facade_color"])
    for nm_ in ("facb", "facc", "facd"):
        flat[nm_] = _srgb(mc[nm_])
    ratio = (ext * size) / max(1.0, layout["ppm"])       # píxeles del atlas por píxel de la imagen de la abertura
    gr = regs["gls"]
    glass_col = tuple(float(x) for x in gr["base"].reshape(-1, 3).mean(0)) if gr is not None else _srgb(mc["glass"])
    for t in layout["tiles"]:
        k, W, H = t["k"], t["W"], t["H"]
        fr = _cs1_sample(regs[frm], flat[frm], W, H, ratio) if k == "win" else _cs1_sample(regs["trim"], flat["trim"], W, H, ratio)
        lf = _cs1_sample(regs.get(k if k in ("door", "gar", "rail") else "trim"), flat.get(k, flat["trim"]), W, H, ratio)
        d, nr, gl, il = _cs1_tile(k, t["wq"] * 0.05, t["hq"] * 0.05, W, H, fr, lf, glass_col, t["v"])
        y0, x0 = t["y"], t["x"]
        out["BaseColor"][y0:y0 + H, x0:x0 + W, :3] = d
        out["Normal"][y0:y0 + H, x0:x0 + W, :3] = nr
        out["MaskMap"][y0:y0 + H, x0:x0 + W, 3] = gl
        illum[y0:y0 + H, x0:x0 + W] = il
    return out, illum


# --- generación de la malla para CS1 (compacta, junto a los LODs de CS2)
def cs1_names(name):
    return f"{name}_CS1", f"{name}_CS1_LOD"


def cs1_objs(s):
    a, b = cs1_names(s.building_name or "Residencial01")
    oa, ob = bpy.data.objects.get(a), bpy.data.objects.get(b)
    return (oa, ob) if oa is not None and ob is not None else None


def _cs1_build(p, shape, lod, mats, budget, qmax=2.0, hi_ratio=1.0, lo_ratio=0.9):
    """Malla de un nivel para CS1 ajustada a `budget` triángulos (contando todo, vidrio incluido). Devuelve (malla, q)."""
    p["_cs1"] = True
    p["lod1_tier"] = 6

    def build(q):
        p["_q"] = q
        bm = build_bmesh_shape(p, lod, shape)
        me = bpy.data.meshes.new("_cs1_tmp")
        try:
            bm.to_mesh(me)
        finally:
            bm.free()
        for m_ in mats:
            me.materials.append(m_)
        clean_hidden_faces(me)
        tris = sum(len(pl.vertices) - 2 for pl in me.polygons)
        return me, tris

    try:
        best = None
        me, t = build(qmax)
        if t <= budget:
            return me, qmax, t
        bpy.data.meshes.remove(me)
        me0, t0 = build(0.0)
        if t0 >= budget and lod == 2:       # el LOD no tiene lugar ni para las ventanas: se dibujan solo en la textura de cerca
            p["_cs1_nowin"] = True
            bpy.data.meshes.remove(me0)
            me0, t0 = build(0.0)
        if t0 >= budget:
            return me0, 0.0, t0
        qa, ta, qb, tb = qmax, t, 0.0, t0
        best = (me0, 0.0, t0)
        lo = lo_ratio * budget
        for _ in range(8):
            q = qb + (budget * 0.97 - tb) * (qa - qb) / max(1, ta - tb)
            q = min(max(q, qb + 0.04 * (qa - qb)), qa - 0.04 * (qa - qb))
            me, t = build(q)
            if t <= budget and t >= best[2]:
                bpy.data.meshes.remove(best[0])
                best = (me, q, t)
            else:
                bpy.data.meshes.remove(me)
            if t > budget:
                qa, ta = q, t
            else:
                qb, tb = q, t
            if lo <= t <= budget:
                break
        return best
    finally:
        p.pop("_cs1", None)
        p.pop("_cs1_nowin", None)
        p.pop("_q", None)


def cs1_generate(context, s, report=None):
    """Arma la malla principal y el LOD para CS1 (ventanas, puertas y marcos van en la imagen, no en la geometría)
    y las deja al lado de los tres LODs de CS2. Devuelve un dict con el resumen."""
    _prog_step(context, 0.03, "Preparando la malla de CS1")
    objs = _ensure_lods(context, s)
    if objs is None:
        regenerate(context)
        objs = _ensure_lods(context, s)
    name = s.building_name or "Residencial01"
    p = props_to_params(s)
    shape = load_shape_for(s)
    p["width"], p["depth"] = shape["size"].x, shape["size"].y
    p["building_name"] = name
    p["_atlas"], p["_ext"] = bake_context(p)
    mats = get_materials(p)
    nm_main, nm_lod = cs1_names(name)
    _prog_step(context, 0.1, "Armando la malla principal")
    me1, q1, t1 = _cs1_build(p, shape, 1, mats, int(s.cs1_tris))
    _prog_step(context, 0.3, "Armando el LOD")
    me2, q2, t2 = _cs1_build(p, shape, 2, mats, int(s.cs1_lod_tris), hi_ratio=1.0)
    _prog_step(context, 0.4, "Ordenando las UV")
    size = int(p.get("tex_size", 1024))
    atlas, ext = bake_context(p)
    keys = set(cs1_openings(me1)) | set(cs1_openings(me2))
    layout = cs1_layout(atlas, size, min(96.0, max(48.0, ext * size * 2.2)), keys)
    for me in (me1, me2):
        cs1_assign_uv(me, layout)
    # vista en Blender: una sola imagen para todas las ranuras
    _prog_step(context, 0.5, "Pintando las texturas")
    arrays = texture_arrays(p, size)
    painted, illum = cs1_paint(arrays, layout, p)
    pmaps = cs1_maps(painted, scale_d=False)
    _prog_step(context, 0.6, "Horneando sombras de la malla")
    me1_src, hi_uv1 = None, None
    if getattr(s, "cs1_ao", True) and float(p.get("bake_ao_str", 0.8)) > 0:
        try:     # igual que en la exportación: UV únicas y sombras horneadas, así Blender muestra lo que sale al juego
            baked, ume = cs1_bake_unique(
                context, me1, me1, p,
                {"d": (pmaps["d"], "sRGB"), "n": (pmaps["n"], "Non-Color"), "s": (pmaps["s"], "Non-Color")}, cs1_main_size(size),
                ao=(float(p.get("bake_ao_str", 0.8)), float(p.get("bake_dist", 2.5)), max(8, int(p.get("bake_samples", 32)))))
            me1_src, me1 = me1, ume
            pmaps = dict(pmaps, **baked)
            hi_uv1 = me1.uv_layers[0].name
        except Exception as e:
            print("CS1: no se pudieron hornear las sombras de la vista:", e)
    _prog_step(context, 0.8, "Horneando la textura del LOD")
    pm = cs1_preview_material(name, pmaps)
    lod_pm = None
    try:     # el LOD se ve con su textura horneada desde la malla principal (ventanas, puertas, balcones...)
        limg, umesh = cs1_bake_lod(context, me1, me2, p, pmaps["d"], bake_lod_sizes(size)[2], hi_uv=hi_uv1)
        bpy.data.meshes.remove(me2)
        me2 = umesh
        lod_pm = cs1_lod_preview_material(name, limg)
    except Exception as e:
        print("CS1: no se pudo hornear la vista del LOD:", e)
    _prog_step(context, 0.95, "Colocando en la escena")
    lm = bpy.data.materials.get(nm_lod + "_mat") or bpy.data.materials.new(nm_lod + "_mat")
    lm.diffuse_color = (*p["facade_color"], 1.0)
    for me, m_ in ((me1, pm), (me2, lod_pm or pm), (me1_src, pm)):
        if me is None:
            continue
        me.materials.clear()
        for _ in range(len(MAT_ORDER) + 5 if m_ is pm else 1):
            me.materials.append(m_)
        if m_ is not pm:
            for pl in me.polygons:
                pl.material_index = 0
    coll = get_collection(context, f"CS2_{name}")
    gap = p["width"] * 1.4
    center = shape["center"]
    made = []
    for me, nm, slot in ((me1, nm_main, 3), (me2, nm_lod, 4)):
        old = bpy.data.objects.get(nm)
        if old is not None:
            om = old.data
            bpy.data.objects.remove(old, do_unlink=True)
            if om.users == 0:
                bpy.data.meshes.remove(om)
        me.name = nm
        ob = bpy.data.objects.new(nm, me)
        coll.objects.link(ob)
        ob.location = (center.x + slot * gap, center.y, center.z)
        made.append(ob)
    if me1_src is not None:      # la malla con UV de atlas se conserva: de ella sale la exportación
        sn = nm_main + "_atlas"
        prev = bpy.data.meshes.get(sn)
        if prev is not None and prev is not me1_src:
            bpy.data.meshes.remove(prev)
        me1_src.name = sn
        me1_src.use_fake_user = True
        made[0]["cs1_src_mesh"] = me1_src.name
    else:
        made[0].pop("cs1_src_mesh", None)
    made[0]["cs1_layout"] = json.dumps(layout)
    made[0]["cs1_info"] = json.dumps({"tris": t1, "lod_tris": t2, "q": q1, "lq": q2})
    return {"tris": t1, "lod_tris": t2, "lost": layout["lost"], "objs": made}


def cs1_lod_preview_material(name, arr):
    """Material de vista previa del LOD de CS1 con su imagen horneada."""
    size = arr.shape[0]
    nm = f"CS2_CS1_{name}_lod_d"
    old = bpy.data.images.get(nm)
    if old is not None:
        bpy.data.images.remove(old)
    img = bpy.data.images.new(nm, size, size, alpha=False)
    img.colorspace_settings.name = "sRGB"
    img.pixels.foreach_set(arr.astype("float32").ravel())
    img.use_fake_user = True
    try:
        img.pack()
    except Exception:
        _log_exc("cs1_lod_preview_material")
    img.update()
    mname = f"{name}_CS1_LOD_mat"
    m = bpy.data.materials.get(mname)
    if m is not None:
        bpy.data.materials.remove(m)
    m = bpy.data.materials.new(mname)
    m.use_nodes = True
    nt = m.node_tree
    bs = nt.nodes.get("Principled BSDF")
    t = nt.nodes.new("ShaderNodeTexImage")
    t.image = img
    nt.links.new(t.outputs["Color"], bs.inputs["Base Color"])
    bs.inputs["Roughness"].default_value = 0.8
    m.diffuse_color = (0.7, 0.7, 0.7, 1.0)
    return m


def cs1_preview_material(name, maps):
    """Material de vista previa con la imagen de CS1 (color, normal y brillo) para verla en Blender."""
    size = maps["d"].shape[0]
    imgs = {}
    for key in ("d", "n", "s"):
        nm = f"CS2_CS1_{name}_{key}"
        old = bpy.data.images.get(nm)
        if old is not None:
            bpy.data.images.remove(old)
        img = bpy.data.images.new(nm, size, size, alpha=False)
        img.colorspace_settings.name = "sRGB" if key == "d" else "Non-Color"
        img.pixels.foreach_set(maps[key].astype("float32").ravel())
        img.use_fake_user = True
        try:
            img.pack()
        except Exception:
            _log_exc("cs1_preview_material")
        img.update()
        imgs[key] = img
    mname = f"{name}_CS1_mat"
    m = bpy.data.materials.get(mname)
    if m is not None:
        bpy.data.materials.remove(m)
    m = bpy.data.materials.new(mname)
    m.use_nodes = True
    nt = m.node_tree
    for nd in list(nt.nodes):
        nt.nodes.remove(nd)
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    bs = nt.nodes.new("ShaderNodeBsdfPrincipled")
    nt.links.new(bs.outputs["BSDF"], out.inputs["Surface"])
    tex = {}
    for key in ("d", "n", "s"):
        t = nt.nodes.new("ShaderNodeTexImage")
        t.image = imgs[key]
        tex[key] = t
    nt.links.new(tex["d"].outputs["Color"], bs.inputs["Base Color"])
    nm_ = nt.nodes.new("ShaderNodeNormalMap")
    nt.links.new(tex["n"].outputs["Color"], nm_.inputs["Color"])
    nt.links.new(nm_.outputs["Normal"], bs.inputs["Normal"])
    inv = nt.nodes.new("ShaderNodeMath")
    inv.operation = "SUBTRACT"
    inv.inputs[0].default_value = 1.0
    nt.links.new(tex["s"].outputs["Color"], inv.inputs[1])
    nt.links.new(inv.outputs["Value"], bs.inputs["Roughness"])
    m.diffuse_color = (0.7, 0.7, 0.7, 1.0)
    return m


def cs1_main_size(size):
    """Tamaño del mapa de la malla principal de CS1 con UV únicas: más fino que el atlas (hasta 4096)."""
    return int(min(4096, max(2048, size * 2)))


def cs1_bake_unique(context, hi_me, lo_me, p, srcs, size, hi_uv=None, ao=None):
    """Hornea mapas desde `hi_me` (UV `hi_uv`, por defecto la primera) a UV únicas (una isla por cara) de una copia de
    `lo_me`. `srcs` = {clave: (arreglo RGBA, 'sRGB' | 'Non-Color')}. `ao` = (fuerza, distancia, rayos) o None: aplica
    la oclusión ambiental al mapa 'd'. Devuelve ({clave: RGBA}, malla con las UV únicas como UV0)."""
    import numpy as np
    sc = context.scene
    bk = sc.render.bake
    keep = (sc.render.engine, sc.cycles.samples, bk.margin, bk.margin_type, bk.use_clear, bk.use_selected_to_active,
            bk.cage_extrusion, bk.max_ray_distance)
    lo_me = lo_me.copy()
    hi = hi_me.copy()
    made_o, made_i, made_m = [], [], []
    world_tmp = None
    try:
        make_unique_uv(lo_me, dict(p, bake_share=False), size)
        base_name = next(l.name for l in lo_me.uv_layers if l.name != "CS2_unique")
        if hi_uv is None:
            hi_uv = next((l.name for l in hi.uv_layers if l.name != "CS2_unique"), hi.uv_layers[0].name)
        aomap = None
        if ao is not None and ao[0] > 0:
            lo_me["cs2_bake_margin"] = int(p.get("bake_margin", 8))
            aomap = compute_ao_map(lo_me, "CS2_unique", size, float(ao[1]), int(ao[2]))
        tgt_img = bpy.data.images.new("cs2_cs1_lod_tgt", size, size, alpha=True)
        tgt_img.colorspace_settings.name = "Non-Color"
        made_i.append(tgt_img)

        def mat_for(name):
            m = bpy.data.materials.new(name)
            m.use_nodes = True
            nt = m.node_tree
            for nd in list(nt.nodes):
                nt.nodes.remove(nd)
            made_m.append(m)
            return m, nt

        mh, nth = mat_for("cs2_cs1_hi")
        out = nth.nodes.new("ShaderNodeOutputMaterial")
        em = nth.nodes.new("ShaderNodeEmission")
        uvn = nth.nodes.new("ShaderNodeUVMap")
        uvn.uv_map = hi_uv
        tx = nth.nodes.new("ShaderNodeTexImage")
        tx.interpolation = "Linear"
        nth.links.new(uvn.outputs["UV"], tx.inputs["Vector"])
        nth.links.new(tx.outputs["Color"], em.inputs["Color"])
        nth.links.new(em.outputs["Emission"], out.inputs["Surface"])
        ml, ntl = mat_for("cs2_cs1_lo")
        outl = ntl.nodes.new("ShaderNodeOutputMaterial")
        eml = ntl.nodes.new("ShaderNodeEmission")
        ntl.links.new(eml.outputs["Emission"], outl.inputs["Surface"])
        tg = ntl.nodes.new("ShaderNodeTexImage")
        tg.image = tgt_img
        ntl.nodes.active = tg
        for me, mm in ((hi, mh), (lo_me, ml)):
            me.materials.clear()
            me.materials.append(mm)
            for pl in me.polygons:
                pl.material_index = 0
        lo_me.uv_layers.active = lo_me.uv_layers["CS2_unique"]
        lo_me.uv_layers["CS2_unique"].active_render = True
        oh = bpy.data.objects.new("cs2_cs1_hi", hi)
        ol = bpy.data.objects.new("cs2_cs1_lo", lo_me)
        for o in (oh, ol):
            sc.collection.objects.link(o)
            made_o.append(o)
        for v in list(context.view_layer.objects):
            try:
                if v is not None:
                    v.select_set(False)
            except (RuntimeError, ReferenceError):
                pass
        oh.select_set(True)
        ol.select_set(True)
        context.view_layer.objects.active = ol
        sc.render.engine = "CYCLES"
        sc.cycles.device = "CPU"
        sc.cycles.samples = 4
        bk.margin, bk.margin_type, bk.use_clear = bake_dilate_px(p, size), "EXTEND", True
        bk.use_selected_to_active = True
        bk.cage_extrusion = 0.6
        bk.max_ray_distance = 3.5
        if sc.world is None:
            world_tmp = bpy.data.worlds.new("cs2_bake_world")
            sc.world = world_tmp
        res = {}
        for key, (arr, cspace) in srcs.items():
            asz = arr.shape[0]
            # el espacio de color va ANTES de cargar los píxeles (si no, la imagen queda en negro)
            im = bpy.data.images.new("cs2_cs1_src_" + key, asz, asz, alpha=True)
            im.colorspace_settings.name = cspace
            im.pixels.foreach_set(arr.astype("float32").ravel())
            im.update()
            made_i.append(im)
            tx.image = im
            tgt_img.colorspace_settings.name = cspace
            context.view_layer.update()
            bpy.ops.object.bake(type="EMIT")
            a = np.empty(size * size * 4, np.float32)
            tgt_img.pixels.foreach_get(a)
            a = a.reshape(size, size, 4)
            if key == "d" and aomap is not None:
                k = float(ao[0])
                a[..., :3] *= (1.0 - k + k * aomap)[..., None]
            a[..., 3] = 1.0
            res[key] = a.copy()
        lo_me.uv_layers.remove(lo_me.uv_layers[base_name])
        lo_me.uv_layers[0].name = "UVMap"
        bpy.data.objects.remove(ol, do_unlink=True)
        made_o.remove(ol)
        out_mesh, lo_me = lo_me, None
        return res, out_mesh
    finally:
        sc.render.engine, sc.cycles.samples = keep[0], keep[1]
        bk.margin, bk.margin_type, bk.use_clear, bk.use_selected_to_active = keep[2], keep[3], keep[4], keep[5]
        bk.cage_extrusion, bk.max_ray_distance = keep[6], keep[7]
        for o in made_o:
            try:
                bpy.data.objects.remove(o, do_unlink=True)
            except ReferenceError:
                pass
        for me in (hi, lo_me):
            if me is not None:
                try:
                    bpy.data.meshes.remove(me)
                except (ReferenceError, RuntimeError):
                    pass
        for im in made_i:
            bpy.data.images.remove(im)
        for m in made_m:
            bpy.data.materials.remove(m)
        if world_tmp is not None:
            sc.world = None
            bpy.data.worlds.remove(world_tmp)


def cs1_bake_lod(context, hi_me, lod_me, p, d_arr, lsize, hi_uv=None):
    """Imagen del LOD de CS1 horneada desde la malla principal (con todo el detalle) a las UV únicas del LOD."""
    res, um = cs1_bake_unique(context, hi_me, lod_me, p, {"d": (d_arr, "sRGB")}, lsize, hi_uv=hi_uv)
    return res["d"], um


def export_building_cs1(context, s, out, p, snow=True, scale=1.0):
    """Exporta para CS1 a <out>/CS1/<nombre>/: <nombre>.fbx y <nombre>_lod.fbx con sus mapas _d/_n/_s/_i
    (y _lod_d), a partir de las mallas CS1 ya generadas. Una sola malla y un material por FBX."""
    name = s.building_name or "Residencial01"
    asset = cs1_name(name)
    objs = cs1_objs(s)
    if objs is None:
        raise RuntimeError("Primero generá la malla para CS1")
    main, lodo = objs
    layout = json.loads(main["cs1_layout"])
    folder = os.path.join(out, "CS1", asset)
    os.makedirs(folder, exist_ok=True)
    size = layout["size"]
    p = dict(p, building_name=name)
    p["_atlas"], p["_ext"] = bake_context(p)
    _prog_step(context, 0.05, "Preparando las texturas de CS1")
    arrays = texture_arrays(p, size)
    painted, illum = cs1_paint(arrays, layout, p)
    _prog_step(context, 0.2, "Armando la malla principal")
    made, info = [], {"asset": asset, "folder": folder}
    coll = context.scene.collection
    try:
        # --- principal
        src_me = bpy.data.meshes.get(main.get("cs1_src_mesh") or "") or main.data
        me = src_me.copy()
        made.append(("mesh", me))
        mat = bpy.data.materials.new(asset)
        mat.use_nodes = True
        made.append(("mat", mat))
        bm = bmesh.new()
        bm.from_mesh(me)
        layers = list(bm.loops.layers.uv)
        keep = next((l for l in layers if l.name != "CS2_unique"), layers[0])
        for l in layers:
            if l is not keep:
                bm.loops.layers.uv.remove(l)
        for f in bm.faces:
            f.material_index = 0
        if snow:
            try:
                ca = bm.loops.layers.float_color.new("Col")
                for f in bm.faces:
                    for lp in f.loops:
                        lp[ca] = (1.0, 1.0, 128.0 / 255.0, 1.0)
            except Exception:
                _log_exc("export_building_cs1")
        bm.to_mesh(me)
        bm.free()
        me.materials.clear()
        me.materials.append(mat)
        ob = bpy.data.objects.new(asset, me)
        coll.objects.link(ob)
        made.append(("obj", ob))
        info["verts"] = len(me.loops)
        info["tris"] = sum(len(pl.vertices) - 2 for pl in me.polygons)
        maps = cs1_maps(painted)
        maps["i"] = _cs1_gray(illum)
        _prog_step(context, 0.35, "Horneando sombras")
        hi_me, hi_uv, size_out = me, None, size
        if getattr(s, "cs1_ao", True) and float(p.get("bake_ao_str", 0.8)) > 0:
            # oclusión ambiental: la malla principal pasa a UV únicas (una isla por cara) con las sombras horneadas
            srcs = {"d": (maps["d"], "sRGB"), "n": (maps["n"], "Non-Color"), "s": (maps["s"], "Non-Color"),
                    "i": (maps["i"], "Non-Color")}
            msize = cs1_main_size(size)
            baked, ume = cs1_bake_unique(context, me, me, p, srcs, msize,
                                         ao=(float(p.get("bake_ao_str", 0.8)), float(p.get("bake_dist", 2.5)),
                                             max(8, int(p.get("bake_samples", 32)))))
            made.append(("mesh", ume))
            ob.data = ume
            me = ume
            me.materials.clear()
            me.materials.append(mat)
            for pl in me.polygons:
                pl.material_index = 0
            maps.update(baked)
            hi_me, hi_uv = me, me.uv_layers[0].name
            info["ao"] = True
            size_out = msize
        info["verts"] = len(me.loops)
        info["tris"] = sum(len(pl.vertices) - 2 for pl in me.polygons)
        _prog_step(context, 0.7, "Escribiendo las texturas")
        write_arrays(folder, asset, maps, size_out)
        # --- LOD con UV únicas entre 0 y 1
        lme = lodo.data.copy()
        made.append(("mesh", lme))
        lsize = bake_lod_sizes(size)[2]
        limg, umesh = cs1_bake_lod(context, hi_me, lme, p, maps["d"], lsize, hi_uv=hi_uv)
        made.append(("mesh", umesh))
        umesh.materials.clear()
        lmat = bpy.data.materials.new(asset + "_lod")
        lmat.use_nodes = True
        made.append(("mat", lmat))
        umesh.materials.append(lmat)
        for pl in umesh.polygons:
            pl.material_index = 0
        lob = bpy.data.objects.new(asset + "_lod", umesh)
        coll.objects.link(lob)
        made.append(("obj", lob))
        info["lod_verts"] = len(umesh.loops)
        info["lod_tris"] = sum(len(pl.vertices) - 2 for pl in umesh.polygons)
        _prog_step(context, 0.85, "Escribiendo el LOD")
        write_arrays(folder, asset + "_lod", {"d": limg}, lsize)
        context.view_layer.update()
        extra = {"global_scale": float(scale)}
        for obj_, fn in ((ob, asset + ".fbx"), (lob, asset + "_lod.fbx")):
            try:
                export_fbx(os.path.join(folder, fn), [obj_], colors_type="LINEAR", **extra)
            except TypeError:
                export_fbx(os.path.join(folder, fn), [obj_], **extra)
        with open(os.path.join(folder, "LEEME_CS1.txt"), "w", encoding="utf-8") as fh:
            fh.write(CS1_README.format(asset=asset))
    finally:
        for kind, it in made:
            try:
                if kind == "obj":
                    bpy.data.objects.remove(it, do_unlink=True)
            except ReferenceError:
                pass
        for kind, it in made:
            try:
                if kind == "mesh":
                    bpy.data.meshes.remove(it)
                elif kind == "mat":
                    bpy.data.materials.remove(it)
            except ReferenceError:
                pass
    return info


CS1_README = """Edificio para Cities: Skylines 1 ({asset})
=====================================
1. Copiá TODO el contenido de esta carpeta a:
   %LocalAppData%\\Colossal Order\\Cities_Skylines\\Addons\\Import
   (pegá esa ruta en el Explorador de archivos).
2. En el juego: Menú principal > Editor de recursos > Nuevo recurso > Edificio.
3. Elegí {asset}.fbx. El juego busca solo {asset}_lod.fbx y los mapas
   ({asset}_d.png, _n, _s, _i y {asset}_lod_d.png) al lado del FBX.
4. Con el texto de calidad de texturas en "Alta" para verlas sin reducir.

Mapas: _d color · _n normal (+X +Y) · _s especular (las ventanas >205 muestran el interior)
       _i iluminación (128-255 = ventanas que se prenden de noche al azar) · _c variación de color (si hay)
Si el edificio sale chico o girado, cambiá la "Escala FBX" en el panel y volvé a exportar.
"""


def export_dir_for(s, operator):
    """Resuelve la carpeta de exportación; None (con error reportado) si no se puede."""
    if s.export_dir.startswith("//") and not bpy.data.filepath:
        operator.report({"ERROR"}, "Guardá el .blend primero o elegí una carpeta absoluta")
        return None
    return bpy.path.abspath(s.export_dir)


# ----------------------------------------------------------------------------
# Piezas de ejemplo (ventanas y puerta de 2 m x 2 m)
# ----------------------------------------------------------------------------
def piece_material(name, color):
    m = bpy.data.materials.get(name)
    if m is None:
        m = bpy.data.materials.new(name)
        if m.node_tree is None:
            m.use_nodes = True
        b = m.node_tree.nodes.get("Principled BSDF")
        if b:
            b.inputs["Base Color"].default_value = (*color, 1.0)
    return m


def _quad(bm, pts, mat):
    bm.faces.new([bm.verts.new(c) for c in pts]).material_index = mat


def build_sample_piece(kind):
    """Pieza de ejemplo de pocos polígonos (solo caras visibles).

    Material 0 = marco, 1 = vidrio, 2 = puerta. Exterior hacia -Y, base en z=0, 2 m x 2 m.
    """
    bm = bmesh.new()
    t, yf, yb = 0.12, 0.03, 0.10
    x0, x1, z0, z1 = -1.0, 1.0, 0.0, 2.0
    ix0, ix1, iz0, iz1 = x0 + t, x1 - t, z0 + t, z1 - t
    # frente del marco (4 tiras) y rebordes hacia el fondo
    _quad(bm, [(x0, yf, z0), (x1, yf, z0), (ix1, yf, iz0), (ix0, yf, iz0)], 0)
    _quad(bm, [(x1, yf, z0), (x1, yf, z1), (ix1, yf, iz1), (ix1, yf, iz0)], 0)
    _quad(bm, [(x1, yf, z1), (x0, yf, z1), (ix0, yf, iz1), (ix1, yf, iz1)], 0)
    _quad(bm, [(x0, yf, z1), (x0, yf, z0), (ix0, yf, iz0), (ix0, yf, iz1)], 0)
    _quad(bm, [(ix0, yf, iz0), (ix1, yf, iz0), (ix1, yb, iz0), (ix0, yb, iz0)], 0)
    _quad(bm, [(ix1, yf, iz0), (ix1, yf, iz1), (ix1, yb, iz1), (ix1, yb, iz0)], 0)
    _quad(bm, [(ix1, yf, iz1), (ix0, yf, iz1), (ix0, yb, iz1), (ix1, yb, iz1)], 0)
    _quad(bm, [(ix0, yf, iz1), (ix0, yf, iz0), (ix0, yb, iz0), (ix0, yb, iz1)], 0)
    # vidrio (o la hoja, en la puerta)
    _quad(bm, [(ix0, yb, iz0), (ix1, yb, iz0), (ix1, yb, iz1), (ix0, yb, iz1)], 2 if kind == "D" else 1)
    if kind == "A":  # parante vertical
        m = 0.04
        _quad(bm, [(-m, yf, iz0), (m, yf, iz0), (m, yf, iz1), (-m, yf, iz1)], 0)
        _quad(bm, [(-m, yf, iz0), (-m, yf, iz1), (-m, yb, iz1), (-m, yb, iz0)], 0)
        _quad(bm, [(m, yf, iz0), (m, yb, iz0), (m, yb, iz1), (m, yf, iz1)], 0)
    elif kind == "B":  # travesaño horizontal y alféizar que sobresale
        zm0, zm1 = 1.26, 1.34
        _quad(bm, [(ix0, yf, zm0), (ix1, yf, zm0), (ix1, yf, zm1), (ix0, yf, zm1)], 0)
        _quad(bm, [(ix0, yf, zm1), (ix1, yf, zm1), (ix1, yb, zm1), (ix0, yb, zm1)], 0)
        _quad(bm, [(ix0, yf, zm0), (ix0, yb, zm0), (ix1, yb, zm0), (ix1, yf, zm0)], 0)
        add_box(bm, 0, -0.04, 0.04, 2.3, 0.2, 0.08, 0, skip=("+y",))
    return bm


def create_sample_pieces(context):
    wcoll = get_collection(context, "CS2_Ventanas")
    dcoll = get_collection(context, "CS2_Puertas")
    mats = [piece_material("CS2_pieza_marco", (0.8, 0.78, 0.72)),
            piece_material("CS2_pieza_vidrio", (0.16, 0.28, 0.40)),
            piece_material("CS2_pieza_puerta", (0.30, 0.18, 0.10))]
    for i, (name, coll, kind) in enumerate([("Ventana_A", wcoll, "A"), ("Ventana_B", wcoll, "B"),
                                            ("Puerta_A", dcoll, "D")]):
        if bpy.data.objects.get(name) is not None:
            continue
        bm = build_sample_piece(kind)
        me = bpy.data.meshes.new(name)
        bm.to_mesh(me)
        bm.free()
        for m in mats:
            me.materials.append(m)
        ob = bpy.data.objects.new(name, me)
        coll.objects.link(ob)
        ob.location = (-16.0 - 3.0 * i, 0.0, 0.0)  # al costado, para verlas y editarlas
    return wcoll, dcoll


# Estado de la generación con progreso (operador modal CS2_OT_generate)
_GEN = {"busy": False, "cancel": False, "frac": 0.0, "msg": ""}


_PENDING = [False]    # hay cambios en el panel que todavía no se aplicaron al edificio (botón «Actualizar»)


_QV = {"timer": False}


def quick_view_apply(s):
    """Vista rápida: pone el atlas de repetición (UV0) en los materiales originales. Devuelve False si hay texturas horneadas asignadas."""
    objs = _existing_lods(s)
    if not objs:
        return False
    if any(m is not None and m.get("cs2_orig") for ob in objs for m in ob.data.materials):
        return False
    update_preview(dict(props_to_params(s), pbr_preview=True))
    return True


def _quick_schedule():
    """Reagrupa los cambios de materiales y actualiza la vista una sola vez (así arrastrar un control no la recalcula 50 veces)."""
    if _QV["timer"]:
        return
    _QV["timer"] = True

    def run():
        _QV["timer"] = False
        try:
            if _GEN["busy"]:
                return None
            for sc in bpy.data.scenes:
                s = getattr(sc, "cs2_building", None)
                if s is not None and s.pbr_preview and _main_obj(s) is not None:
                    quick_view_apply(s)
        except Exception as e:
            log_error("vista_rapida", e)
        return None
    bpy.app.timers.register(run, first_interval=0.3)


def _pbr_preview_changed(self, context):
    if _ROOF_LOCK[0] or _GEN["busy"]:
        return
    objs = _existing_lods(self)
    if not objs:
        return
    try:
        if self.pbr_preview:
            _quick_schedule()
        elif not any(m is not None and m.get("cs2_orig") for ob in objs for m in ob.data.materials):
            update_preview(dict(props_to_params(self), pbr_preview=False))
    except Exception as e:
        log_error("vista_rapida", e)


_AUTO = {"t": 0.0}


def _auto_refresh_schedule():
    """Rehace el edificio un ratito después del último cambio (si llegan varios cambios seguidos, una sola vez)."""
    _AUTO["t"] = time.time()
    try:
        if not bpy.app.timers.is_registered(_auto_refresh_tick):
            bpy.app.timers.register(_auto_refresh_tick, first_interval=0.4)
    except Exception:
        _log_exc("auto_refresh_registro")


def _auto_refresh_tick():
    if time.time() - _AUTO["t"] < 0.35:
        return 0.15
    if _GEN["busy"] or not _PENDING[0]:
        return None
    try:
        s = bpy.context.scene.cs2_building
        if _main_obj(s) is not None and s.auto_refresh:
            regenerate(bpy.context, live=True)
            _PENDING[0] = False
            _redraw_all()
    except Exception as e:
        log_error("actualizar_solo", e)
    return None


def _on_change(self, context):
    if _ROOF_LOCK[0] or _GEN["busy"]:
        return
    if _main_obj(self) is not None:
        _PENDING[0] = True
        if getattr(self, "pbr_preview", False):
            _quick_schedule()
        if getattr(self, "auto_refresh", False):
            _auto_refresh_schedule()
    # no se regenera solo: el edificio se rehace con el botón «Actualizar edificio» (la opción «en vivo» se retiró)


# ----------------------------------------------------------------------------
# Propiedades, operadores y panel
# ----------------------------------------------------------------------------
DORMER_ITEMS = [("0", "Sin buhardillas", ""), ("1", "Una agua", "Techito de una sola pendiente"),
                ("2", "Dos aguas (frontón)", "Con frontón triangular"), ("3", "Cuatro aguas (capuchina)", "Techo con faldones"),
                ("4", "Arqueada (bóveda)", "Techo curvo")]
PAN_MODE_ITEMS = [("1", "Ninguno", ""), ("2", "Al azar", "Algunas ventanas con paño de color, otras no"),
                  ("3", "Ajedrez", "Alternado como tablero"), ("4", "Columnas alternas", "Una columna sí y otra no"),
                  ("5", "Todas", "Todas las ventanas con paño"),
                  ("6", "Crujías alternas", "Una columna de ventanas sí y otra no, de color y de punta a punta de la pared"),
                  ("7", "Crujías cada tres", "Una columna de cada tres, de punta a punta de la pared"),
                  ("8", "Crujías de los bordes", "Solo la primera y la última columna, de punta a punta"),
                  ("9", "Crujías al azar", "Columnas enteras al azar (según la proporción)")]
PAN_MAT_ITEMS = [("1", "Material B", ""), ("2", "Material C", ""), ("3", "Material D", ""),
                 ("4", "B y C alternados", ""), ("5", "B, C y D al azar", "")]
SURF_MAT_ITEMS = [("1", "Molduras", ""), ("2", "Fachada (A)", ""), ("3", "Material B", ""),
                  ("4", "Material C", ""), ("5", "Material D", "")]
SLT_MODE_ITEMS = [("0", "Ninguna", ""), ("1", "Verticales", ""), ("2", "Horizontales", "")]
WALL_ROT_ITEMS = [("0", "Sin girar", ""), ("1", "90°", "Un cuarto de vuelta"), ("2", "180°", "Media vuelta"), ("3", "270°", "Tres cuartos de vuelta")]
WALL_MAT_ITEMS = [("0", "Igual al edificio", ""), ("1", "A (fachada)", ""), ("2", "B (ladrillo)", ""),
                  ("3", "C (gris oscuro)", ""), ("4", "D (madera)", "")]
GLOBAL_MAT_ITEMS = WALL_MAT_ITEMS[1:]
_ROOF_LOCK = [False]
_ROOF_SNAP = [None]   # techo general guardado mientras se edita el techo de caras sueltas


def _roof_set_values(s, d):
    _ROOF_LOCK[0] = True
    try:
        for k, v in d.items():
            if k == "type":
                s.r_type = v
            elif k in ("ribs", "cap", "flip", "nopar", "nocor"):
                setattr(s, "r_" + k, bool(v))
            elif k in ("ridge", "chcap", "dm", "dm_side", "capm"):
                setattr(s, "r_" + k, str(int(v)))
            else:
                setattr(s, "r_" + k, v)
    finally:
        _ROOF_LOCK[0] = False


def _grid_toggle_update(self, context):
    try:
        if self.show_grid:
            make_grid(context)
        else:
            remove_grid()
    except Exception as e:
        print("CS2: cuadrícula:", e)


_SCOPE_BUSY = [False]


def _scope_update(self, context):
    """Al pasar a 'Caras elegidas' se entra solo a Editar forma; al volver a 'Todo el edificio' se sale. (El alcance siempre sigue
    al modo de Blender: lo mantiene al día _depsgraph_handler, también cuando se usa Tab.)"""
    if _SCOPE_BUSY[0]:
        return
    _SCOPE_BUSY[0] = True
    try:
        obj = self.shape_object
        editing = obj is not None and obj.type == "MESH" and obj.mode == "EDIT"
        if self.edit_scope == "FACES" and obj is not None and obj.type == "MESH" and not editing:
            bpy.ops.cs2.edit_shape()
        elif self.edit_scope == "ALL" and editing:
            bpy.ops.cs2.edit_shape()
    except Exception as e:
        print("CS2: alcance:", e)
    finally:
        _SCOPE_BUSY[0] = False
ROOF_INT_PROPS = {"nopar": int, "nocor": int, "ridge": int, "flip": int, "ribs": int, "cap": int, "chn": int, "chcap": int, "dm": int, "dm_n": int, "dm_side": int, "capm": int}


def roof_values(s):
    """Techo general que usa el edificio (con 'Caras elegidas' activo, el guardado al entrar)."""
    return roof_values_ui(s)


def roof_values_ui(s):
    """Valores de techo que muestra el panel, como dict (tipos ya convertidos)."""
    d = {}
    for k in ROOF_KEYS_INT + ROOF_KEYS_FLOAT:
        if k == "set":
            continue
        v = getattr(s, "r_" + k)
        d[k] = int(v) if k in ROOF_INT_PROPS else (v if k == "type" else float(v))
    return d


def _roof_preset_apply(self, context):
    data = ROOF_PRESETS.get(self.r_preset)
    if not data:
        return
    _ROOF_LOCK[0] = True
    try:
        vals = {"peak": 0.0, "dm": 0, "sd_fr": -1.0, "sd_bk": -1.0, "sd_lf": -1.0, "sd_rg": -1.0}
        vals.update(data[1])
        for k, v in vals.items():
            if k == "type":
                self.r_type = v
            elif k in ("ribs", "cap", "flip"):
                setattr(self, "r_" + k, bool(v))
            elif k in ("ridge", "chcap", "dm", "dm_side", "capm"):
                setattr(self, "r_" + k, str(int(v)))
            else:
                setattr(self, "r_" + k, v)
    finally:
        _ROOF_LOCK[0] = False
    _on_change(self, context)


class CS2BuildingProps(bpy.types.PropertyGroup):
    building_name: StringProperty(name="Nombre", default="Residencial01", update=_on_change)
    theme: EnumProperty(name="Prefijo de tema (Theme prefix)", default="NA",
                        items=[("NONE", "Ninguno", "Sin prefijo de tema"), ("NA", "NA", "Norteamérica"), ("EU", "EU", "Europa")])
    level: EnumProperty(name="Nivel de aparición (Spawn level)", default="1",
                        items=[("NONE", "Sin spawn", "Sin nivel: el nombre del asset no lleva L1-L5 (edificios de servicio o que no aparecen solos)")]
                              + [(str(i), f"L{i}", f"Nivel {i} (el juego usa sobre todo 1, 3 y 5)") for i in range(1, 6)])
    asset_category: EnumProperty(name="Categoría (Asset category)", default="ZONED",
                                 items=[("ZONED", "Zonificado (Zoned building)", "Aparece solo al pintar una zona: lote de 1x2 a 6x6 celdas y niveles L1-L5"),
                                        ("SERVICE", "Servicio (Service building)", "Lo coloca el jugador (escuelas, bomberos, etc.): sin niveles y sin el límite de 6x6 celdas"),
                                        ("SIGNATURE", "Emblemático (Signature building)", "Emblemático: lo coloca el jugador, único, sin niveles y sin límite de lote")])
    zone_type: EnumProperty(name="Tipo", default="RESLOW",
                            items=[(k, v[0], f"Palabra clave del nombre para las ventanas: {v[1] or 'ninguna'}") for k, v in ZONE_TYPES.items()])
    cells_x: IntProperty(name="Celdas de frente (Frontage)", default=3, min=1, max=100)
    cells_z: IntProperty(name="Celdas de fondo (Depth)", default=2, min=1, max=100)
    custom_size: BoolProperty(name="Medidas en metros", default=False)
    width: FloatProperty(name="Ancho (m)", default=23.0, min=4.0, max=120.0)
    depth: FloatProperty(name="Fondo (m)", default=15.0, min=4.0, max=120.0)
    floors: IntProperty(name="Pisos", default=6, min=1, max=40)
    floor_height: FloatProperty(name="Altura de piso (m)", default=3.0, min=2.4, max=5.0, update=_on_change)
    win_w: FloatProperty(name="Ancho (m)", default=1.3, min=0.6, max=6.0, update=_on_change)
    win_h: FloatProperty(name="Alto (m)", default=1.4, min=0.6, max=3.2, update=_on_change)
    win_sill: FloatProperty(name="Altura del antepecho (m)", default=0.9, min=0.0, max=2.0, update=_on_change,
                            description="Desde el piso hasta la base de la ventana")
    win_depth: FloatProperty(name="Profundidad (m)", default=0.25, min=0.0, max=0.8, update=_on_change,
                             description="0 = ventana a ras de la pared")
    door_w_g: FloatProperty(name="Ancho (m)", default=0.0, min=0.0, max=4.0, update=_on_change,
                            description="0 = automático según las ventanas")
    door_h_g: FloatProperty(name="Alto (m)", default=0.0, min=0.0, max=3.5, update=_on_change,
                            description="0 = automático según la altura del piso")
    bal_depth_g: FloatProperty(name="Profundidad (m)", default=1.2, min=0.3, max=3.0, update=_on_change)
    bal_w_g: FloatProperty(name="Ancho (m)", default=0.0, min=0.0, max=8.0, update=_on_change,
                           description="0 = automático según la ventana")
    bal_rail_g: FloatProperty(name="Alto baranda (m)", default=0.9, min=0.3, max=1.5, update=_on_change)
    mat_b_color: FloatVectorProperty(name="Material B (ladrillo)", subtype="COLOR", size=3, min=0.0, max=1.0,
                                     default=MAT_COLORS["facb"], update=_on_change)
    mat_c_color: FloatVectorProperty(name="Material C (gris oscuro)", subtype="COLOR", size=3, min=0.0, max=1.0,
                                     default=MAT_COLORS["facc"], update=_on_change)
    mat_d_color: FloatVectorProperty(name="Material D (madera)", subtype="COLOR", size=3, min=0.0, max=1.0,
                                     default=MAT_COLORS["facd"], update=_on_change)
    pan_mode_g: EnumProperty(name="Paños de color", default="1", items=PAN_MODE_ITEMS, update=_on_change)
    pan_mat_g: EnumProperty(name="Material", default="1", items=PAN_MAT_ITEMS, update=_on_change)
    pan_pad_g: FloatProperty(name="Margen alrededor (m)", default=0.35, min=0.0, max=1.5, update=_on_change,
                             description="Cuánto se extiende el paño alrededor de la ventana")
    pan_prob_g: FloatProperty(name="Proporción", default=0.5, min=0.0, max=1.0, subtype="FACTOR", update=_on_change)
    cas_w_g: FloatProperty(name="Ancho del marco (m)", default=0.0, min=0.0, max=0.8, update=_on_change,
                           description="0 = sin marco saliente")
    cas_d_g: FloatProperty(name="Saliente (m)", default=0.08, min=0.01, max=0.5, update=_on_change)
    cas_mat_g: EnumProperty(name="Material", default="1", items=SURF_MAT_ITEMS, update=_on_change)
    slt_mode_g: EnumProperty(name="Lamas", default="0", items=SLT_MODE_ITEMS, update=_on_change)
    slt_gap_g: FloatProperty(name="Separación (m)", default=0.3, min=0.1, max=1.5, update=_on_change)
    slt_w_g: FloatProperty(name="Grosor (m)", default=0.07, min=0.02, max=0.4, update=_on_change)
    slt_d_g: FloatProperty(name="Saliente (m)", default=0.06, min=0.02, max=0.4, update=_on_change)
    slt_mat_g: EnumProperty(name="Material", default="5", items=SURF_MAT_ITEMS, update=_on_change)
    cw_on_g: BoolProperty(name="Muro cortina en todas las paredes", default=False, update=_on_change,
                          description="Las paredes pasan a ser una cuadrícula de vidrio con perfiles (sin ventanas, puertas ni balcones)")
    cw_mw_g: FloatProperty(name="Ancho del módulo (m)", default=1.5, min=0.4, max=6.0, update=_on_change)
    cw_mh_g: FloatProperty(name="Alto del módulo (m)", default=0.0, min=0.0, max=6.0, update=_on_change,
                           description="0 = un módulo por piso")
    cw_pt_g: FloatProperty(name="Grosor de perfiles (m)", default=0.06, min=0.0, max=0.4, update=_on_change,
                           description="0 = sin perfiles (solo vidrio)")
    cw_pd_g: FloatProperty(name="Saliente de perfiles (m)", default=0.08, min=0.02, max=0.6, update=_on_change)
    cw_mmat_g: EnumProperty(name="Material de perfiles", default="4", items=SURF_MAT_ITEMS, update=_on_change)
    cw_band_g: FloatProperty(name="Banda opaca por piso (m)", default=0.45, min=0.0, max=1.5, update=_on_change,
                             description="Franja llena a la altura de cada losa; 0 = vidrio continuo")
    cw_bmat_g: EnumProperty(name="Material de la banda", default="4", items=SURF_MAT_ITEMS, update=_on_change)
    cw_edge_g: FloatProperty(name="Margen opaco a los lados (m)", default=0.0, min=0.0, max=8.0, update=_on_change,
                             description="Paños llenos a los costados de cada pared (como una columna de revestimiento)")
    cw_finn_g: IntProperty(name="Aleta cada N módulos", default=0, min=0, max=12, update=_on_change,
                           description="0 = sin aletas verticales")
    cw_find_g: FloatProperty(name="Saliente de aletas (m)", default=0.35, min=0.05, max=1.5, update=_on_change)
    cw_fint_g: FloatProperty(name="Grosor de aletas (m)", default=0.12, min=0.03, max=0.6, update=_on_change)
    cw_fmat_g: EnumProperty(name="Material de aletas", default="1", items=SURF_MAT_ITEMS, update=_on_change)
    cw_ht_g: FloatProperty(name="Perfil horizontal: grosor (m)", default=0.0, min=0.0, max=0.4, update=_on_change,
                           description="Grosor de los perfiles horizontales; 0 = igual al de los verticales")
    cw_hd_g: FloatProperty(name="Perfil horizontal: saliente (m)", default=0.0, min=0.0, max=0.6, update=_on_change,
                           description="Cuánto salen los perfiles horizontales; 0 = igual al de los verticales")
    cw_prof_g: EnumProperty(name="Forma de perfiles", default="0", update=_on_change,
                            items=[("0", "Cuadrado", ""), ("1", "Con tapa plana", "Perfil con una placa fina al frente"),
                                   ("2", "Redondo", "Perfil de sección redondeada"),
                                   ("3", "Sin perfiles", "Vidrio estructural: paños de vidrio sin perfiles a la vista")])
    cw_pat_g: EnumProperty(name="Patrón de módulos", default="0", update=_on_change,
                           items=[("0", "Todos iguales", ""), ("1", "Angosto y ancho alternados", "Alterna módulos de dos anchos"),
                                  ("2", "Paño opaco cada N módulos", "Cada N módulos va uno lleno (ventilación o tablero)")])
    cw_patk_g: FloatProperty(name="Proporción del módulo angosto", default=0.6, min=0.2, max=1.0, update=_on_change,
                             description="Ancho del módulo angosto respecto al ancho (1 = todos iguales)")
    cw_patn_g: IntProperty(name="Un paño cada N módulos", default=3, min=2, max=12, update=_on_change)
    cw_doorw_g: FloatProperty(name="Puerta de acceso: ancho (m)", default=0.0, min=0.0, max=6.0, update=_on_change,
                              description="Puerta de vidrio en el centro de la planta baja; 0 = sin puerta")
    cw_baseh_g: FloatProperty(name="Zócalo: alto (m)", default=0.0, min=0.0, max=3.0, update=_on_change,
                              description="Franja llena en la base del muro; 0 = sin zócalo")
    cw_basemat_g: EnumProperty(name="Zócalo: material", default="4", items=SURF_MAT_ITEMS, update=_on_change)
    cw_caph_g: FloatProperty(name="Remate superior: alto (m)", default=0.0, min=0.0, max=3.0, update=_on_change,
                             description="Antepecho o cornisa en lo alto del muro; 0 = sin remate")
    cw_capo_g: FloatProperty(name="Remate superior: saliente (m)", default=0.0, min=0.0, max=1.5, update=_on_change,
                             description="Cuánto sobresale el remate (0 = al ras, como un antepecho)")
    cw_capmat_g: EnumProperty(name="Remate superior: material", default="4", items=SURF_MAT_ITEMS, update=_on_change)
    cw_cornw_g: FloatProperty(name="Perfil de esquina: ancho (m)", default=0.0, min=0.0, max=1.0, update=_on_change,
                              description="Perfil vertical grueso en los extremos de cada pared; 0 = sin perfil")
    ground_mat_g: EnumProperty(name="Material de la planta baja", default="0", items=WALL_MAT_ITEMS, update=_on_change,
                               description="Revestimiento distinto en los pisos de abajo (por ejemplo, zócalo de ladrillo o local comercial). «Igual al edificio» lo apaga")
    ground_n_g: IntProperty(name="Pisos con ese material", default=1, min=1, max=3, update=_on_change,
                            description="Cuántos pisos desde el suelo llevan el material de la planta baja")
    wall_mat_g: EnumProperty(name="Material de las paredes", default="1", items=GLOBAL_MAT_ITEMS, update=_on_change,
                             description="Material base de todas las paredes (cada pared puede cambiarlo)")
    frm_mat_g: EnumProperty(name="Marcos de ventana", default="1", items=SURF_MAT_ITEMS, update=_on_change,
                            description="Material de los marcos y divisiones de las ventanas (aluminio negro = Material C)")
    show_grid: BoolProperty(name="Mostrar cuadrícula de Cities 2", default=False, update=_grid_toggle_update,
                            description="Dibuja la cuadrícula de celdas de 8 m con tu lote al centro")
    edit_scope: EnumProperty(name="Alcance", default="ALL", update=_scope_update,
                             items=[("ALL", "Todo el edificio", "Los cambios valen para todo el edificio"),
                                    ("FACES", "Caras elegidas", "Los cambios valen solo para las caras elegidas en Editar forma")])
    balconies: EnumProperty(
        name="Balcones", default="alternate", update=_on_change,
        items=[("none", "Sin balcones", ""), ("alternate", "Alternados", ""),
               ("all", "En todas las ventanas", ""), ("random", "Al azar", "")])
    balcony_prob: FloatProperty(name="Probabilidad de balcón", default=0.5, min=0.0, max=1.0,
                                subtype="FACTOR", update=_on_change)
    trim_color: FloatVectorProperty(name="Color molduras", subtype="COLOR", size=3, min=0.0, max=1.0,
                                    default=(0.81, 0.77, 0.68), update=_on_change)
    roof_color: FloatVectorProperty(name="Color techo", subtype="COLOR", size=3, min=0.0, max=1.0,
                                    default=(0.15, 0.115, 0.097), update=_on_change)
    door_color: FloatVectorProperty(name="Color puertas", subtype="COLOR", size=3, min=0.0, max=1.0,
                                    default=(0.066, 0.039, 0.021), update=_on_change)
    roof_geo_tiles: BoolProperty(name="Tejas con geometría aunque haya textura", default=False, update=_on_change,
                                 description="Por defecto, con textura PBR en el techo no se modelan las tejas (las da el mapa normal y no suman triángulos)")
    gar_color: FloatVectorProperty(name="Color portón de garaje", subtype="COLOR", size=3, min=0.0, max=1.0,
                                   default=(0.55, 0.55, 0.52), update=_on_change)
    rail_color: FloatVectorProperty(name="Color barandas y rejas", subtype="COLOR", size=3, min=0.0, max=1.0,
                                    default=(0.81, 0.77, 0.68), update=_on_change)
    string_courses: BoolProperty(name="Cornisas entre pisos", default=True, update=_on_change)
    # --- techos (valores globales; cada cara de techo puede tener los suyos) ---
    r_preset: EnumProperty(name="Modelo de techo", items=[(k, v[0], "") for k, v in ROOF_PRESETS.items()],
                           default="FLAT", update=lambda self, ctx: _roof_preset_apply(self, ctx))
    r_type: EnumProperty(name="Tipo", items=ROOF_TYPES, default="FLAT", update=_on_change)
    r_pitch: FloatProperty(name="Inclinación (°)", default=28.0, min=3.0, max=75.0, update=_on_change)
    r_oe: FloatProperty(name="Alero (m)", default=0.45, min=0.0, max=2.5, update=_on_change,
                        description="Cuánto sobresale el techo de la pared en los lados bajos")
    r_ov: FloatProperty(name="Alero de hastial (m)", default=0.30, min=0.0, max=2.5, update=_on_change,
                        description="Cuánto sobresale en los extremos (dos aguas y una agua)")
    r_t: FloatProperty(name="Espesor (m)", default=0.12, min=0.0, max=0.6, update=_on_change)
    r_ridge: EnumProperty(name="Caballete", default="0", update=_on_change,
                          items=[("0", "Por el lado largo", ""), ("1", "Por el lado corto", "")])
    r_flip: BoolProperty(name="Invertir caída", default=False, update=_on_change,
                         description="Una agua: cambia hacia qué lado baja el techo")
    r_peak: FloatProperty(name="Cumbrera descentrada", default=0.0, min=-0.8, max=0.8, subtype="FACTOR", update=_on_change,
                          description="Dos aguas: corre la cumbrera hacia un lado (0 = al medio). Un lado largo y suave, otro corto y empinado")
    r_dm: EnumProperty(name="Buhardillas", default="0", update=_on_change, items=DORMER_ITEMS)
    r_dm_n: IntProperty(name="Cantidad por lado", default=2, min=1, max=8, update=_on_change)
    r_dm_side: EnumProperty(name="Lado", default="0", update=_on_change,
                            items=[("0", "Ambos lados", ""), ("1", "Un lado", ""), ("2", "El otro lado", "")])
    r_dm_w: FloatProperty(name="Ancho (m)", default=1.6, min=0.9, max=4.0, update=_on_change)
    r_dm_h: FloatProperty(name="Alto de la pared (m)", default=1.7, min=1.1, max=3.0, update=_on_change)
    r_dm_q: FloatProperty(name="Posición en la pendiente", default=0.25, min=0.0, max=0.7, subtype="FACTOR", update=_on_change,
                          description="0 = cerca del alero, más = más cerca de la cumbrera")
    r_pitch2: FloatProperty(name="Inclinación de la tapa (°)", default=18.0, min=3.0, max=60.0, update=_on_change)
    r_brk: FloatProperty(name="Ancho del faldón (m)", default=1.3, min=0.3, max=4.0, update=_on_change)
    r_ribs: BoolProperty(name="Tejas en relieve", default=True, update=_on_change)
    r_nopar: BoolProperty(name="Sin parapet", default=False, update=_on_change,
                          description="Quita el parapet (el muro bajo) del borde del techo plano")
    r_nocor: BoolProperty(name="Sin cornisa", default=False, update=_on_change,
                          description="Quita la cornisa (la moldura bajo el borde) del techo plano")
    r_par_h: FloatProperty(name="Alto del parapet (m)", default=0.8, min=0.1, max=3.0, update=_on_change)
    r_par_t: FloatProperty(name="Grosor del parapet (m)", default=0.25, min=0.05, max=1.0, update=_on_change)
    r_cor_h: FloatProperty(name="Alto de la cornisa (m)", default=0.3, min=0.05, max=1.5, update=_on_change)
    r_cor_o: FloatProperty(name="Vuelo de la cornisa (m)", default=0.15, min=0.0, max=0.8, update=_on_change,
                           description="Cuánto sobresale la cornisa de la pared")
    r_rib_gap: FloatProperty(name="Separación de tejas (m)", default=0.22, min=0.08, max=0.8, update=_on_change)
    r_rib_h: FloatProperty(name="Relieve (m)", default=0.035, min=0.005, max=0.15, precision=3, update=_on_change)
    r_cap: BoolProperty(name="Cumbrera y limahoyas", default=True, update=_on_change)
    r_capm: EnumProperty(name="Material de cumbrera", default="0", update=_on_change,
                         items=[("0", "Molduras (claro)", ""), ("1", "Techo (igual a las tejas)", "")])
    r_sd_fr: FloatProperty(name="Frente (−Y)", default=-1.0, min=-1.0, max=3.0, update=_on_change,
                           description="Alero de ese lado del edificio. -1 = usa el alero general")
    r_sd_bk: FloatProperty(name="Atrás (+Y)", default=-1.0, min=-1.0, max=3.0, update=_on_change,
                           description="Alero de ese lado del edificio. -1 = usa el alero general")
    r_sd_lf: FloatProperty(name="Izquierda (−X)", default=-1.0, min=-1.0, max=3.0, update=_on_change,
                           description="Alero de ese lado del edificio. -1 = usa el alero general")
    r_sd_rg: FloatProperty(name="Derecha (+X)", default=-1.0, min=-1.0, max=3.0, update=_on_change,
                           description="Alero de ese lado del edificio. -1 = usa el alero general")
    r_chn: IntProperty(name="Chimeneas", default=0, min=0, max=2, update=_on_change)
    r_chpos: FloatProperty(name="Posición a lo largo (0-1)", default=0.3, min=0.05, max=0.95, update=_on_change)
    r_chside: FloatProperty(name="Posición lado (−1…1)", default=0.0, min=-0.9, max=0.9, update=_on_change)
    r_chw: FloatProperty(name="Ancho (m)", default=0.7, min=0.3, max=2.0, update=_on_change)
    r_chd: FloatProperty(name="Fondo (m)", default=0.7, min=0.3, max=2.0, update=_on_change)
    r_chh: FloatProperty(name="Altura sobre el techo (m)", default=1.0, min=0.2, max=3.0, update=_on_change)
    r_chcap: EnumProperty(name="Remate", default="1", update=_on_change,
                          items=[("0", "Sin remate", ""), ("1", "Losa", ""), ("2", "Losa y 2 tiros", "")])
    string_style: EnumProperty(name="Estilo", items=STRING_STYLES, default="BAND", update=_on_change)
    string_every: IntProperty(name="Cada cuántos pisos", default=1, min=1, max=6, update=_on_change,
                              description="1 = en todos los pisos, 2 = un piso por medio, etc.")
    string_z: FloatProperty(name="Distancia bajo el piso (m)", default=0.275, min=-0.5, max=1.5, update=_on_change,
                            description="Qué tan abajo del nivel de cada piso queda el centro de la moldura")
    string_h: FloatProperty(name="Alto de cornisa (m)", default=0.15, min=0.04, max=0.6, update=_on_change)
    string_out: FloatProperty(name="Saliente de cornisa (m)", default=0.05, min=0.0, max=0.3, update=_on_change)
    facade_color: FloatVectorProperty(name="Color fachada", subtype="COLOR", size=3,
                                      min=0.0, max=1.0, default=(0.69, 0.58, 0.41), update=_on_change)
    roof: EnumProperty(name="Techo", default="flat", update=_on_change,
                       items=[("flat", "Plano con parapet", ""), ("gable", "A dos aguas", "")])
    roof_height: FloatProperty(name="Altura caballete (m)", default=2.5, min=0.5, max=8.0, update=_on_change)
    roof_box: BoolProperty(name="Caja de escalera", default=True, update=_on_change)
    uv_tile: FloatProperty(name="Metros por textura", default=4.0, min=0.5, max=20.0, update=_on_change)
    win_rooms: EnumProperty(name="Ventanas de noche", default="rooms", update=_on_change,
                            items=[("rooms", "Interiores variados", "Cada ventana mira a un cuarto distinto (01-23)"),
                                   ("on", "Siempre encendidas", "Todas usan el color 'siempre encendida'"),
                                   ("off", "Siempre apagadas", "Todas usan el color 'siempre apagada'")])
    tex_size: EnumProperty(name="Tamaño de textura (Texture size)", default="1024",
                           items=[("512", "512", ""), ("1024", "1024", ""), ("2048", "2048", ""), ("4096", "4096", "")])
    auto_refresh: BoolProperty(name="Actualizar solo", default=True,
                               description="Rehace el edificio un momento después de cada cambio en los componentes (si está apagado, usá «Actualizar edificio»)")
    live_update: BoolProperty(name="Actualizar en vivo (se regenera solo al cambiar algo)", default=False,
                              description="Si está apagado, el edificio solo cambia al hacer clic en Generar")
    clean_on_generate: BoolProperty(name="Optimizar al generar (quitar caras ocultas)", default=True,
                                    description="Al apretar Generar o exportar quita las caras que no se ven. Al mover "
                                                "valores del panel la vista previa no se limpia, para que responda rápido")
    clean_on_export: BoolProperty(name="Limpiar caras ocultas al exportar", default=True,
                                  description="Antes de exportar quita las caras que no se ven (tapadas o mirando al suelo)")
    separate_lods: BoolProperty(name="Ver los tres niveles de detalle lado a lado", default=True, update=_on_change)
    export_dir: StringProperty(name="Carpeta", subtype="DIR_PATH", default="//salida/")
    game: EnumProperty(name="Juego", items=[("CS2", "Cities: Skylines 2", "FBX por LOD y texturas para CS2"),
                                           ("CS1", "Cities: Skylines (1)", "FBX principal + _lod y mapas _d/_n/_s/_i")],
                       default="CS2")
    cs1_tris: IntProperty(name="Triángulos de la malla CS1", default=4000, min=300, max=30000,
                          description="Presupuesto de la malla principal para CS1 (las ventanas son imagen, no cuentan como detalle)")
    cs1_lod_tris: IntProperty(name="Triángulos del LOD CS1", default=300, min=40, max=2000,
                              description="Presupuesto de la malla _lod de CS1")
    cs1_snow: BoolProperty(name="Sin nieve 3D", default=True,
                           description="Color de vértice con azul 128: el juego no genera nieve en 3D sobre el edificio")
    cs1_ao: BoolProperty(name="Sombras (oclusión) en la malla principal", default=True,
                         description="Hornea la oclusión ambiental en el difuso de CS1 como en CS2 (usa Fuerza y Distancia de la oclusión). "
                                     "La malla principal pasa a UV únicas por cara")
    cs1_scale: FloatProperty(name="Escala FBX", default=1.0, min=0.01, max=100.0,
                             description="Si en el juego el edificio sale chico o grande, probá 100 o 0.01")

    # variación
    seed: IntProperty(name="Variante", default=1, min=0, max=99999, update=_on_change)
    variation: FloatProperty(name="Variación", default=0.0, min=0.0, max=1.0,
                             subtype="FACTOR", update=_on_change,
                             description="Cuánto varían ventanas, color y caja de escalera según la semilla")
    floors_jitter: IntProperty(name="Variar pisos (±)", default=0, min=0, max=10, update=_on_change)

    # piezas propias
    window_collection: PointerProperty(name="Ventanas", type=bpy.types.Collection, update=_on_change)
    door_collection: PointerProperty(name="Puertas", type=bpy.types.Collection, update=_on_change)
    piece_random: BoolProperty(name="Elegir pieza al azar", default=True, update=_on_change)
    window_style: EnumProperty(name="Ventanas", items=_items_window, update=_on_change,
                               description="Estilo de todas las ventanas del edificio")
    door_style: EnumProperty(name="Puertas", items=_items_door, update=_on_change,
                             description="Estilo de todas las puertas del edificio")
    balcony_style: EnumProperty(name="Balcones", items=_items_balcony, update=_on_change,
                                description="Estilo de todos los balcones del edificio")

    # lote
    batch_count: IntProperty(name="Cantidad", default=6, min=1, max=60)
    batch_spacing: FloatProperty(name="Separación (m)", default=4.0, min=0.0, max=40.0)
    batch_export: BoolProperty(name="Exportar FBX de cada uno", default=False)

    # modo forma
    mode: EnumProperty(name="Modo", default="shape", update=_on_change,
                       items=[("box", "Caja", "Edificio rectangular paramétrico"),
                              ("shape", "Forma", "Edificio a partir de una malla base que podés extruir")])
    shape_object: PointerProperty(name="Objeto", type=bpy.types.Object, poll=_poll_mesh, update=_on_change)
    shape_recalc: BoolProperty(name="Corregir normales de la forma", default=True, update=_on_change)
    curve_deg: FloatProperty(name="Unir curvas hasta (°)", default=25.0, min=0.0, max=60.0, update=_on_change,
                             description="Las paredes vecinas que giran menos que esto entre sí se tratan como una sola pared curva: ventanas, puertas y "
                                         "balcones se reparten a lo largo de todo el arco. 0 = cada cara es una pared. "
                                         "Cada faceta tiene que medir al menos el ancho de la ventana + 0,3 m para recibir una")
    snap_floors: BoolProperty(name="Ajustar alturas a pisos", default=True, update=_on_change,
                              description="Redondea las alturas de la forma a múltiplos de la altura de piso")
    balcony_walls: EnumProperty(name="Balcones en", default="front", update=_on_change,
                                items=[("front", "Frente (-Y)", ""), ("all", "Todas las paredes", "")])


# ----------------------------------------------------------------------------
# Editor por pared: ajustes guardados en las caras de la forma base
# ----------------------------------------------------------------------------
_WALL_LOCK = [False]   # evita que cargar la interfaz vuelva a escribir en las caras
_LAST_ACTIVE = {"key": None}

WALL_GROUPS = {
    "win": dict(flag=FLAG_WIN, use="use_win", ints={"win_count": "cs2_w_count"},
                floats={"win_w": "cs2_w_w", "win_h": "cs2_w_h", "win_sill": "cs2_w_sill",
                        "win_depth": "cs2_w_depth", "win_off": "cs2_w_off"}),
    "door": dict(flag=FLAG_DOOR, use="use_door", ints={},
                 floats={"door_pos": "cs2_d_pos", "door_w": "cs2_d_w", "door_h": "cs2_d_h",
                         "door_depth": "cs2_d_depth", "door_off": "cs2_d_off"}),
    "pan": dict(flag=FLAG_PAN, use="use_pan", ints={"pan_mode": "cs2_p_mode", "pan_mat": "cs2_p_mat"},
                floats={"pan_pad": "cs2_p_pad", "pan_prob": "cs2_p_prob"}),
    "cas": dict(flag=FLAG_CAS, use="use_cas", ints={"cas_mat": "cs2_c_mat"},
                floats={"cas_w": "cs2_c_w", "cas_d": "cs2_c_d"}),
    "slt": dict(flag=FLAG_SLT, use="use_slt", ints={"slt_mode": "cs2_s_mode", "slt_mat": "cs2_s_mat"},
                floats={"slt_gap": "cs2_s_gap", "slt_w": "cs2_s_w", "slt_d": "cs2_s_d"}),
    "cw": dict(flag=FLAG_CW, use="use_cw", ints={"cw_bmat": "cs2_k_bmat", "cw_mmat": "cs2_k_mmat", "cw_fmat": "cs2_k_fmat",
                                                  "cw_finn": "cs2_k_finn",
                                                  **{"cw_" + k: "cs2_k_" + k for k in _CW_NEW_I}},
               floats={"cw_mw": "cs2_k_mw", "cw_mh": "cs2_k_mh", "cw_pt": "cs2_k_pt", "cw_pd": "cs2_k_pd",
                       "cw_band": "cs2_k_band", "cw_edge": "cs2_k_edge", "cw_find": "cs2_k_find", "cw_fint": "cs2_k_fint",
                       **{"cw_" + k: "cs2_k_" + k for k in _CW_NEW_F}}),
    "por": dict(flag=FLAG_POR, use="use_por", ints={"por_" + k: "cs2_o_" + k for k in PORCH_INTS},
                floats={"por_" + k: "cs2_o_" + k for k in PORCH_FLOATS}),
    "bal": dict(flag=FLAG_BAL, use="use_bal", ints={},
                floats={"bal_depth": "cs2_b_depth", "bal_w": "cs2_b_w", "bal_rail": "cs2_b_rail",
                        "bal_off": "cs2_b_off"}),
}


for _k, _g in NEW_GROUPS.items():
    WALL_GROUPS[_k] = dict(flag=_g["flag"], use=_g["use"], ints={f"{_k}_{n_}": _g["pre"] + n_ for n_ in _g["ints"]},
                           floats={f"{_k}_{n_}": _g["pre"] + n_ for n_ in _g["floats"]})


def _edit_shape_obj(context):
    """El objeto forma si está en modo edición; si no, None."""
    s = context.scene.cs2_building
    obj = s.shape_object
    if s.mode != "shape" or obj is None or obj.type != "MESH" or obj.mode != "EDIT":
        return None
    return obj


def _selected_faces(bm):
    return [f for f in bm.faces if f.select]


def _wall_update(group):
    def upd(self, context):
        if _WALL_LOCK[0]:
            return
        obj = _edit_shape_obj(context)
        if obj is None:
            return
        bm = bmesh.from_edit_mesh(obj.data)
        lays = wall_layers(bm, create=True)  # primero las capas: crearlas invalida las caras ya tomadas
        faces = _selected_faces(bm)
        if not faces:
            return
        g = WALL_GROUPS[group]
        for f in faces:
            fl = f[lays["cs2_flags"]]
            f[lays["cs2_flags"]] = (fl | g["flag"]) if getattr(self, g["use"]) else (fl & ~g["flag"])
            for prop, attr in g["ints"].items():
                f[lays[attr]] = int(getattr(self, prop))
            for prop, attr in g["floats"].items():
                f[lays[attr]] = float(getattr(self, prop))
            if group == "win":  # 0 = automática, -1 = sin ventanas, n = cantidad fija
                f[lays["cs2_w_count"]] = 0 if self.win_auto else (int(self.win_count) if self.win_count > 0 else -1)
            if group == "bal" and getattr(self, "use_bal") and self.bal_mode == "0":
                _bal_auto_mode(self, lays, faces)
        bmesh.update_edit_mesh(obj.data, loop_triangles=False, destructive=False)
        _on_change(context.scene.cs2_building, context)
    return upd


def _bal_auto_mode(self, lays, faces):
    """Al personalizar balcones en caras sueltas, 'según opciones generales' no pondría ninguno: pasa a 'todos los pisos'."""
    _WALL_LOCK[0] = True
    try:
        self.bal_mode = "2"
    finally:
        _WALL_LOCK[0] = False
    for f in faces:
        if not int(f[lays["cs2_bal_mode"]]):
            f[lays["cs2_bal_mode"]] = 2


def _mode_update(attr):
    def upd(self, context):
        if _WALL_LOCK[0]:
            return
        obj = _edit_shape_obj(context)
        if obj is None:
            return
        bm = bmesh.from_edit_mesh(obj.data)
        lays = wall_layers(bm, create=True)
        faces = _selected_faces(bm)
        if not faces:
            return
        val = int(getattr(self, {"cs2_door_mode": "door_mode", "cs2_bal_mode": "bal_mode", "cs2_f_mat": "f_mat", "cs2_f_rot": "f_rot", "cs2_k_mode": "cw_mode"}[attr]))
        for f in faces:
            f[lays[attr]] = val
        bmesh.update_edit_mesh(obj.data, loop_triangles=False, destructive=False)
        _on_change(context.scene.cs2_building, context)
    return upd


def _style_attr_update(attr, prop):
    def upd(self, context):
        if _WALL_LOCK[0]:
            return
        obj = _edit_shape_obj(context)
        if obj is None:
            return
        bm = bmesh.from_edit_mesh(obj.data)
        lays = wall_layers(bm, create=True)
        faces = _selected_faces(bm)
        if not faces:
            return
        val = wall_style_id(getattr(self, prop))
        for f in faces:
            f[lays[attr]] = val
        if attr == "cs2_b_style" and getattr(self, prop) != "__GLOBAL__" and self.bal_mode == "0":
            _bal_auto_mode(self, lays, faces)
        bmesh.update_edit_mesh(obj.data, loop_triangles=False, destructive=False)
        _on_change(context.scene.cs2_building, context)
    return upd


class CS2WallProps(bpy.types.PropertyGroup):
    """Valores que se muestran en el panel: son los de la cara activa de la forma."""
    win_style: EnumProperty(name="Estilo de ventana", items=_items_wall_window,
                            update=_style_attr_update("cs2_w_style", "win_style"))
    door_style_w: EnumProperty(name="Estilo de puerta", items=_items_wall_door,
                               update=_style_attr_update("cs2_d_style", "door_style_w"))
    bal_style_w: EnumProperty(name="Estilo de balcón", items=_items_wall_balcony,
                              update=_style_attr_update("cs2_b_style", "bal_style_w"))
    use_win: BoolProperty(name="Personalizar ventanas", default=False, update=_wall_update("win"))
    win_auto: BoolProperty(name="Cantidad automática", default=True, update=_wall_update("win"),
                           description="La cantidad se calcula según el ancho de la pared")
    win_count: IntProperty(name="Cantidad por piso", default=3, min=0, max=30, update=_wall_update("win"),
                           description="0 = sin ventanas en esta pared")
    win_w: FloatProperty(name="Ancho (m)", default=1.3, min=0.4, max=6.0, update=_wall_update("win"))
    win_h: FloatProperty(name="Alto (m)", default=1.4, min=0.4, max=3.0, update=_wall_update("win"))
    win_sill: FloatProperty(name="Altura del antepecho (m)", default=0.9, min=0.0, max=2.0,
                            update=_wall_update("win"), description="Desde el piso hasta la base de la ventana")
    win_depth: FloatProperty(name="Profundidad (m)", default=0.25, min=0.0, max=0.8, update=_wall_update("win"),
                             description="0 = ventana a ras de la pared")
    win_off: FloatProperty(name="Mover (m)", default=0.0, min=-6.0, max=6.0, update=_wall_update("win"),
                           description="Desplaza todas las ventanas de la pared a lo largo de ella")

    door_mode: EnumProperty(name="Puerta", default="0", update=_mode_update("cs2_door_mode"),
                            items=[("0", "Automática", "Puerta en el centro de la planta baja del frente"),
                                   ("1", "Sí", "Poner puerta en esta pared"),
                                   ("2", "No", "Sin puerta en esta pared")])
    use_door: BoolProperty(name="Personalizar puerta", default=False, update=_wall_update("door"))
    door_pos: FloatProperty(name="Posición", default=0.5, min=0.0, max=1.0, subtype="FACTOR",
                            update=_wall_update("door"), description="0 = borde izquierdo, 1 = borde derecho")
    door_w: FloatProperty(name="Ancho (m)", default=1.2, min=0.6, max=4.0, update=_wall_update("door"))
    door_h: FloatProperty(name="Alto (m)", default=2.2, min=1.8, max=3.5, update=_wall_update("door"))
    door_depth: FloatProperty(name="Profundidad (m)", default=0.25, min=0.0, max=0.8, update=_wall_update("door"))
    door_off: FloatProperty(name="Mover (m)", default=0.0, min=-12.0, max=12.0, update=_wall_update("door"),
                            description="Desplaza la puerta a lo largo de la pared (se suma a la posición)")

    bal_mode: EnumProperty(name="Balcones", default="0", update=_mode_update("cs2_bal_mode"),
                           items=[("0", "Según opciones generales", ""), ("1", "Ninguno", ""),
                                  ("2", "En todos los pisos", ""), ("3", "Alternados", "")])
    use_bal: BoolProperty(name="Personalizar balcones", default=False, update=_wall_update("bal"))
    bal_depth: FloatProperty(name="Profundidad (m)", default=1.2, min=0.3, max=3.0, update=_wall_update("bal"))
    bal_w: FloatProperty(name="Ancho (m)", default=2.1, min=0.8, max=8.0, update=_wall_update("bal"))
    bal_rail: FloatProperty(name="Alto baranda (m)", default=0.9, min=0.3, max=1.5, update=_wall_update("bal"))
    bal_off: FloatProperty(name="Mover (m)", default=0.0, min=-12.0, max=12.0, update=_wall_update("bal"),
                           description="Desplaza los balcones de la pared a lo largo de ella")
    f_mat: EnumProperty(name="Material de la pared", default="0", items=WALL_MAT_ITEMS,
                        update=_mode_update("cs2_f_mat"))
    f_rot: EnumProperty(name="Giro de la textura", default="0", items=WALL_ROT_ITEMS,
                        update=_mode_update("cs2_f_rot"),
                        description="Gira la textura de esta pared (se suma al giro del material). Se ve al actualizar el edificio")
    use_pan: BoolProperty(name="Personalizar paños", default=False, update=_wall_update("pan"))
    pan_mode: EnumProperty(name="Paños de color", default="2", items=PAN_MODE_ITEMS[1:] if False else PAN_MODE_ITEMS,
                           update=_wall_update("pan"))
    pan_mat: EnumProperty(name="Material", default="1", items=PAN_MAT_ITEMS, update=_wall_update("pan"))
    pan_pad: FloatProperty(name="Margen alrededor (m)", default=0.35, min=0.0, max=1.5, update=_wall_update("pan"))
    pan_prob: FloatProperty(name="Proporción", default=0.5, min=0.0, max=1.0, subtype="FACTOR",
                            update=_wall_update("pan"))
    use_cas: BoolProperty(name="Personalizar marcos", default=False, update=_wall_update("cas"))
    cas_w: FloatProperty(name="Ancho del marco (m)", default=0.25, min=0.0, max=0.8, update=_wall_update("cas"))
    cas_d: FloatProperty(name="Saliente (m)", default=0.08, min=0.01, max=0.5, update=_wall_update("cas"))
    cas_mat: EnumProperty(name="Material", default="1", items=SURF_MAT_ITEMS, update=_wall_update("cas"))
    use_slt: BoolProperty(name="Personalizar lamas", default=False, update=_wall_update("slt"))
    slt_mode: EnumProperty(name="Lamas", default="1", items=SLT_MODE_ITEMS, update=_wall_update("slt"))
    slt_gap: FloatProperty(name="Separación (m)", default=0.3, min=0.1, max=1.5, update=_wall_update("slt"))
    slt_w: FloatProperty(name="Grosor (m)", default=0.07, min=0.02, max=0.4, update=_wall_update("slt"))
    slt_d: FloatProperty(name="Saliente (m)", default=0.06, min=0.02, max=0.4, update=_wall_update("slt"))
    slt_mat: EnumProperty(name="Material", default="5", items=SURF_MAT_ITEMS, update=_wall_update("slt"))
    cw_mode: EnumProperty(name="Muro cortina", default="0", update=_mode_update("cs2_k_mode"),
                          items=[("0", "Según opciones generales", ""), ("1", "Sí, en esta pared", "Reemplaza ventanas, puertas y balcones"),
                                 ("2", "No", "Esta pared lleva ventanas normales")])
    use_cw: BoolProperty(name="Personalizar muro cortina", default=False, update=_wall_update("cw"))
    cw_mw: FloatProperty(name="Ancho del módulo (m)", default=1.5, min=0.4, max=6.0, update=_wall_update("cw"))
    cw_mh: FloatProperty(name="Alto del módulo (m)", default=0.0, min=0.0, max=6.0, update=_wall_update("cw"),
                         description="0 = un módulo por piso")
    cw_pt: FloatProperty(name="Grosor de perfiles (m)", default=0.06, min=0.0, max=0.4, update=_wall_update("cw"))
    cw_pd: FloatProperty(name="Saliente de perfiles (m)", default=0.08, min=0.02, max=0.6, update=_wall_update("cw"))
    cw_mmat: EnumProperty(name="Material de perfiles", default="4", items=SURF_MAT_ITEMS, update=_wall_update("cw"))
    cw_band: FloatProperty(name="Banda opaca por piso (m)", default=0.45, min=0.0, max=1.5, update=_wall_update("cw"))
    cw_bmat: EnumProperty(name="Material de la banda", default="4", items=SURF_MAT_ITEMS, update=_wall_update("cw"))
    cw_edge: FloatProperty(name="Margen opaco a los lados (m)", default=0.0, min=0.0, max=8.0, update=_wall_update("cw"))
    cw_finn: IntProperty(name="Aleta cada N módulos", default=0, min=0, max=12, update=_wall_update("cw"))
    cw_find: FloatProperty(name="Saliente de aletas (m)", default=0.35, min=0.05, max=1.5, update=_wall_update("cw"))
    cw_fint: FloatProperty(name="Grosor de aletas (m)", default=0.12, min=0.03, max=0.6, update=_wall_update("cw"))
    cw_fmat: EnumProperty(name="Material de aletas", default="1", items=SURF_MAT_ITEMS, update=_wall_update("cw"))
    cw_ht: FloatProperty(name="Perfil horizontal: grosor (m)", default=0.0, min=0.0, max=0.4, update=_wall_update("cw"),
                           description="Grosor de los perfiles horizontales; 0 = igual al de los verticales")
    cw_hd: FloatProperty(name="Perfil horizontal: saliente (m)", default=0.0, min=0.0, max=0.6, update=_wall_update("cw"),
                           description="Cuánto salen los perfiles horizontales; 0 = igual al de los verticales")
    cw_prof: EnumProperty(name="Forma de perfiles", default="0", update=_wall_update("cw"),
                            items=[("0", "Cuadrado", ""), ("1", "Con tapa plana", "Perfil con una placa fina al frente"),
                                   ("2", "Redondo", "Perfil de sección redondeada"),
                                   ("3", "Sin perfiles", "Vidrio estructural: paños de vidrio sin perfiles a la vista")])
    cw_pat: EnumProperty(name="Patrón de módulos", default="0", update=_wall_update("cw"),
                           items=[("0", "Todos iguales", ""), ("1", "Angosto y ancho alternados", "Alterna módulos de dos anchos"),
                                  ("2", "Paño opaco cada N módulos", "Cada N módulos va uno lleno (ventilación o tablero)")])
    cw_patk: FloatProperty(name="Proporción del módulo angosto", default=0.6, min=0.2, max=1.0, update=_wall_update("cw"),
                             description="Ancho del módulo angosto respecto al ancho (1 = todos iguales)")
    cw_patn: IntProperty(name="Un paño cada N módulos", default=3, min=2, max=12, update=_wall_update("cw"))
    cw_doorw: FloatProperty(name="Puerta de acceso: ancho (m)", default=0.0, min=0.0, max=6.0, update=_wall_update("cw"),
                              description="Puerta de vidrio en el centro de la planta baja; 0 = sin puerta")
    cw_baseh: FloatProperty(name="Zócalo: alto (m)", default=0.0, min=0.0, max=3.0, update=_wall_update("cw"),
                              description="Franja llena en la base del muro; 0 = sin zócalo")
    cw_basemat: EnumProperty(name="Zócalo: material", default="4", items=SURF_MAT_ITEMS, update=_wall_update("cw"))
    cw_caph: FloatProperty(name="Remate superior: alto (m)", default=0.0, min=0.0, max=3.0, update=_wall_update("cw"),
                             description="Antepecho o cornisa en lo alto del muro; 0 = sin remate")
    cw_capo: FloatProperty(name="Remate superior: saliente (m)", default=0.0, min=0.0, max=1.5, update=_wall_update("cw"),
                             description="Cuánto sobresale el remate (0 = al ras, como un antepecho)")
    cw_capmat: EnumProperty(name="Remate superior: material", default="4", items=SURF_MAT_ITEMS, update=_wall_update("cw"))
    cw_cornw: FloatProperty(name="Perfil de esquina: ancho (m)", default=0.0, min=0.0, max=1.0, update=_wall_update("cw"),
                              description="Perfil vertical grueso en los extremos de cada pared; 0 = sin perfil")


PORCH_ENUMS = {
    "roof": ("Techo", [("1", "Sin techo", ""), ("2", "Una agua", ""), ("3", "Dos aguas (frontón)", ""), ("4", "Cuatro aguas", "")]),
    "rail": ("Baranda", [("1", "Sin baranda", ""), ("2", "Balaustres", ""), ("3", "Sólida", ""), ("4", "Barras", ""), ("5", "Vidrio", "")]),
    "enc": ("Cerramiento", [("1", "Abierto", ""), ("2", "Mosquitero", ""), ("3", "Cerrado vidriado", "")]),
    "stp": ("Escalones", [("1", "Con escalones", ""), ("2", "Sin escalones", "")]),
    "lv": ("Niveles", [("1", "Un nivel", ""), ("2", "Dos niveles", "")]),
    "dmat": ("Material del piso", SURF_MAT_ITEMS),
    "pmat": ("Material de columnas", SURF_MAT_ITEMS),
}
PORCH_NUMS = {
    "d": ("Profundidad (m)", 0.8, 6.0), "w": ("Ancho (m, 0 = toda la pared)", 0.0, 40.0), "off": ("Mover (m)", -20.0, 20.0),
    "ext": ("Extender en esquinas (m)", 0.0, 6.0), "ph": ("Altura de columnas (m)", 2.2, 4.5),
    "dh": ("Altura del piso (m)", 0.0, 1.2), "pitch": ("Inclinación del techo (°)", 3.0, 45.0),
    "sp": ("Separación de columnas (m)", 1.0, 6.0),
    "sz": ("Tamaño general (×)", 0.4, 3.0),
}
PORCH_PRESETS = {
    "FARMER": ("Farmer", dict(roof=2, rail=1, d=1.6, w=4.0, pitch=14, ph=2.5, dh=0.3, dmat=5, pmat=1)),
    "WRAP": ("Envolvente", dict(roof=2, rail=2, d=1.6, w=5.0, pitch=14, ph=2.5, dh=0.4, walls=2, ext=1.0, dmat=5, pmat=1)),
    "GABLED": ("Con frontón", dict(roof=3, rail=1, d=1.5, w=2.6, pitch=38, ph=2.5, dh=0.25, pmat=1)),
    "PORTICO": ("Pórtico", dict(roof=3, rail=1, d=1.2, w=2.2, pitch=38, ph=2.5, dh=0.2, pmat=1, dmat=2)),
    "SHEDP": ("Una agua", dict(roof=2, rail=2, d=1.5, w=3.2, pitch=16, ph=2.5, dh=0.3)),
    "HIPP": ("Cuatro aguas", dict(roof=4, rail=1, d=1.6, w=3.2, pitch=22, ph=2.5, dh=0.3, pmat=1)),
    "VERANDA": ("Veranda", dict(roof=2, rail=2, d=1.8, w=5.0, pitch=14, ph=2.6, dh=0.4, walls=2, ext=1.2, pmat=1)),
    "SCREENED": ("Con mosquitero", dict(roof=2, rail=1, enc=2, d=1.8, w=3.6, pitch=16, ph=2.5, dh=0.3, pmat=5)),
    "ENCLOSED": ("Cerrado vidriado", dict(roof=2, rail=1, enc=3, d=1.8, w=3.6, pitch=16, ph=2.5, dh=0.25, pmat=1)),
    "SLEEPING": ("Dos niveles", dict(roof=2, rail=2, enc=2, lv=2, d=1.8, w=3.6, pitch=14, ph=2.5, dh=0.3, pmat=1)),
    "DECK": ("Terraza (deck)", dict(roof=1, rail=2, d=2.0, w=4.0, dh=0.4, stp=1, pmat=1)),
}


def _porch_update_global(self, context):
    _on_change(self, context)


def _install_porch_props():
    wa = CS2WallProps.__annotations__
    ba = CS2BuildingProps.__annotations__
    ba["por_on_g"] = BoolProperty(name="Porche en las paredes", default=False, update=_on_change)
    ba["por_walls_g"] = EnumProperty(name="Dónde", default="1", update=_on_change,
                                     items=[("1", "Solo el frente", ""), ("2", "Frente y costados", ""), ("3", "Todas las paredes", "")])
    wa["use_por"] = BoolProperty(name="Personalizar porche", default=False, update=_wall_update("por"))
    wa["por_on"] = EnumProperty(name="Porche", default="2", update=_wall_update("por"),
                                items=[("1", "Sin porche", ""), ("2", "Con porche", "")])
    for k, (label, items) in PORCH_ENUMS.items():
        dflt = str(PORCH_DEF[k])
        ba[f"por_{k}_g"] = EnumProperty(name=label, default=dflt, items=items, update=_on_change)
        wa[f"por_{k}"] = EnumProperty(name=label, default=dflt, items=items, update=_wall_update("por"))
    for k, (label, lo, hi) in PORCH_NUMS.items():
        ba[f"por_{k}_g"] = FloatProperty(name=label, default=PORCH_DEF[k], min=lo, max=hi, update=_on_change)
        wa[f"por_{k}"] = FloatProperty(name=label, default=PORCH_DEF[k], min=lo, max=hi, update=_wall_update("por"))


_install_porch_props()


def _install_group_props():
    """Propiedades del edificio (<clave>_<k>_g) y de la pared (<clave>_<k>) de los grupos de la tabla NEW_GROUPS."""
    wa = CS2WallProps.__annotations__
    ba = CS2BuildingProps.__annotations__
    for key, g in NEW_GROUPS.items():
        wa[g["use"]] = BoolProperty(name="Personalizar " + g["label"].lower(), default=False, update=_wall_update(key))
        for k, (label, items, d) in g["ints"].items():
            ba[f"{key}_{k}_g"] = EnumProperty(name=label, default=str(d), items=items, update=_on_change)
            wa[f"{key}_{k}"] = EnumProperty(name=label, default=str(d), items=items, update=_wall_update(key))
        for k, (label, lo, hi, d) in g["floats"].items():
            ba[f"{key}_{k}_g"] = FloatProperty(name=label, default=d, min=lo, max=hi, update=_on_change)
            wa[f"{key}_{k}"] = FloatProperty(name=label, default=d, min=lo, max=hi, update=_wall_update(key))


_install_group_props()


_MATPREV = {"pcoll": None, "items": [("", "(sin biblioteca)", "")], "sets": {}, "root": None, "folders": [], "cache": {}}


def _matprev_coll():
    import bpy.utils.previews
    if _MATPREV["pcoll"] is None:
        _MATPREV["pcoll"] = bpy.utils.previews.new()
    return _MATPREV["pcoll"]


def mat_icon(path):
    """Id de icono (miniatura) de una imagen del disco; 0 si no existe o no se pudo cargar."""
    if not path:
        return 0
    path = bpy.path.abspath(path)
    if not os.path.isfile(path):
        return 0
    pc = _matprev_coll()
    try:
        if path not in pc:
            pc.load(path, path, "IMAGE")
        return pc[path].icon_id
    except Exception:
        _log_exc("icono_material")
        return 0


def pbr_group(rel):
    """Carpeta (categoría) de un set de la biblioteca: la carpeta que lo contiene, o el nombre sin números
    (Bricks001_4K-JPG -> Bricks) si los sets están todos sueltos en la raíz."""
    parts = rel.split("/")
    if len(parts) > 1:
        return "/".join(parts[:-1])
    m = re.match(r"[A-Za-z]+", parts[0])
    return m.group(0).capitalize() if m else "Otros"


def pbr_groups(root):
    """{carpeta: [(ruta relativa, etiqueta)]} de la biblioteca."""
    out = {}
    for rel, lab in scan_pbr_library(root):
        out.setdefault(pbr_group(rel), []).append((rel, lab))
    return out


def _pbr_folder_items(self, context):
    root = bpy.path.abspath(self.pbr_library or "")
    groups = pbr_groups(root) if root else {}
    items = [(g, f"{g.replace('/', ' / ')}  ({len(v)})", "") for g, v in sorted(groups.items(), key=lambda kv: kv[0].lower())]
    _MATPREV["folders"] = items or [("", "(elegí la carpeta de la biblioteca)", "")]
    return _MATPREV["folders"]


def _pbr_grid_items(self, context):
    """Sets de UNA carpeta de la biblioteca con su miniatura (la imagen de color). Solo se cargan las miniaturas de la
    carpeta elegida: abrir toda la biblioteca junta tardaba mucho."""
    root = bpy.path.abspath(self.pbr_library or "")
    grp = self.pbr_folder
    key = (root, grp)
    cached = _MATPREV["cache"].get(key)
    if cached is None:
        sets = dict(pbr_groups(root).get(grp, [])) if root else {}
        items = []
        for i, (rel, lab) in enumerate(sorted(sets.items(), key=lambda kv: kv[1].lower())):
            folder = os.path.join(root, *rel.split("/"))
            col = scan_pbr_set(folder).get("img_color", "")
            short = lab.split(" / ")[-1]
            for tail in ("_4K-JPG", "_2K-JPG", "_1K-JPG", "_8K-JPG", "_4K-PNG", "_2K-PNG", "_1K-PNG", "_8K-PNG"):
                if short.endswith(tail):
                    short = short[:-len(tail)]
            items.append((rel, short, lab, mat_icon(col), i))
        cached = _MATPREV["cache"][key] = items or [("", "(elegí una carpeta de la biblioteca)", "", 0, 0)]
    _MATPREV["items"] = cached
    return cached


def _pbr_lib_changed(self, context):
    _MATPREV["cache"].clear()
    _MATPREV["folders"] = []


def _pbr_grid_apply(self, context):
    rel = self.pbr_grid
    if not rel or self.pbr_target not in PBR_SLOTS:
        return
    folder = os.path.join(bpy.path.abspath(self.pbr_library), *rel.split("/"))
    apply_pbr_set(self, self.pbr_target, folder)
    _on_change(self, context)


def _ms_expand_update(slot):
    """Acordeón: al abrir un material se cierran los demás (la pestaña Texturas queda corta)."""
    def upd(self, context):
        if getattr(self, f"ms_{slot}_expanded"):
            for o in PBR_SLOTS:
                if o != slot and getattr(self, f"ms_{o}_expanded", False):
                    setattr(self, f"ms_{o}_expanded", False)
    return upd


def _install_matslot_props():
    ba = CS2BuildingProps.__annotations__
    for sl in PBR_SLOTS:
        lab = PBR_SLOT_LABEL[sl]
        d = dict(SLOT_DEFAULTS)
        d["gloss"] = ATLAS_GLOSS.get(sl, 0.15)
        d["snow"] = sl != "roof"
        ba[f"ms_{sl}_expanded"] = BoolProperty(name=lab, default=(sl == "facade"), update=_ms_expand_update(sl))
        ba[f"ms_{sl}_mode"] = EnumProperty(name="Fuente", default="FLAT", update=_on_change,
                                           items=[("FLAT", "Color liso", "Usa el color de 'Medidas' y los valores de abajo"),
                                                  ("PBR", "Texturas", "Mapas de imagen (difuso, normal, rugosidad...)")])
        ba[f"ms_{sl}_set"] = StringProperty(name="Set", default="")
        for k, label in PBR_MAP_FIELDS:
            ba[f"ms_{sl}_{k}"] = StringProperty(name=label, default="", subtype="FILE_PATH", update=_on_change)
        ba[f"ms_{sl}_tint"] = FloatVectorProperty(name="Tinte", subtype="COLOR", size=3, min=0.0, max=1.0,
                                                  default=(1.0, 1.0, 1.0), update=_on_change,
                                                  description="Multiplica el difuso (blanco = sin cambio)")
        ba[f"ms_{sl}_tile"] = FloatProperty(name="Repetición cada (m)", default=2.0, min=0.1, max=30.0, update=_on_change,
                                            description="Cuántos metros del edificio cubre una repetición de la textura")
        ba[f"ms_{sl}_rot"] = FloatProperty(name="Giro de la textura (°)", default=0.0, min=-180.0, max=180.0, step=500, precision=0,
                                           update=_on_change,
                                           description="Gira la textura de este material sobre todas sus caras (por ejemplo ladrillo horizontal o vertical). "
                                                       "Se ve al actualizar el edificio. Los ángulos que no son múltiplos de 90° agrandan el espacio que ocupa en el mapa")
        ba[f"ms_{sl}_nstr"] = FloatProperty(name="Fuerza del normal", default=1.0, min=0.0, max=3.0, update=_on_change)
        ba[f"ms_{sl}_flipg"] = BoolProperty(name="Normal DirectX (invertir verde)", default=False, update=_on_change,
                                            description="El juego usa OpenGL. Activalo si el mapa es DirectX")
        ba[f"ms_{sl}_gmode"] = EnumProperty(name="El mapa es", default="ROUGH", update=_on_change,
                                            items=[("ROUGH", "Rugosidad (se invierte)", ""), ("GLOSS", "Brillo / especular", "")])
        ba[f"ms_{sl}_metal"] = FloatProperty(name="Metálico", default=0.0, min=0.0, max=1.0, subtype="FACTOR", update=_on_change)
        ba[f"ms_{sl}_gloss"] = FloatProperty(name="Brillo", default=d["gloss"], min=0.0, max=1.0, subtype="FACTOR", update=_on_change)
        ba[f"ms_{sl}_gmult"] = FloatProperty(name="Brillo del mapa x", default=1.0, min=0.0, max=2.0, update=_on_change)
        ba[f"ms_{sl}_coat"] = FloatProperty(name="Capa (coat)", default=0.0, min=0.0, max=1.0, subtype="FACTOR", update=_on_change,
                                            description="Brillo extra sobre el brillo normal (MaskMap, canal verde)")
        ba[f"ms_{sl}_aostr"] = FloatProperty(name="Sombras suaves en las juntas", default=1.0, min=0.0, max=1.0, subtype="FACTOR", update=_on_change,
                                             description="Cuánto se oscurece el difuso con el mapa de oclusión (el juego no tiene canal de AO)")
        ba[f"ms_{sl}_emis_col"] = FloatVectorProperty(name="Color emisivo", subtype="COLOR", size=3, min=0.0, max=1.0,
                                                      default=(0.0, 0.0, 0.0), update=_on_change)
        ba[f"ms_{sl}_emis_str"] = FloatProperty(name="Intensidad emisiva", default=1.0, min=0.0, max=4.0, update=_on_change)
        ba[f"ms_{sl}_cmask"] = EnumProperty(name="Variación de color", default="0", update=_on_change,
                                            items=[("0", "Sin variación", ""), ("1", "Canal 1 (R)", ""),
                                                   ("2", "Canal 2 (G)", ""), ("3", "Canal 3 (B)", "")],
                                            description="ControlMask: el juego tiñe este material con un set de colores al azar")
        ba[f"ms_{sl}_snow"] = BoolProperty(name="Sin nieve", default=d["snow"], update=_on_change,
                                           description="ControlMask alfa: la nieve automática se quita de este material")
    ba["pbr_preview"] = BoolProperty(name="Vista rápida de texturas", default=True, update=_pbr_preview_changed,
                                     description="Muestra en el modelo, al instante, las texturas que vas eligiendo (se repiten sobre cada superficie). No reemplaza al horneado final")
    ba["pbr_target"] = EnumProperty(name="Aplicar a", default="facade",
                                    items=[(sl, PBR_SLOT_LABEL[sl], "") for sl in PBR_SLOTS],
                                    description="Material que recibe el set que elijas en la grilla")
    ba["pbr_grid"] = EnumProperty(name="Biblioteca", items=_pbr_grid_items, update=_pbr_grid_apply,
                                  description="Elegí un set: se aplica al material de arriba")
    ba["bake_ao"] = BoolProperty(name="Guardar las sombras en la textura", default=False,
                                 description="Cada LOD recibe UV únicas (sin repetir) y sus propias texturas con la oclusión ambiental real del modelo")
    ba["tex_mode"] = EnumProperty(
        name="Modo", items=[("REPEAT", "Rápido (se repite)", "UV instantáneas con la textura repetida: más píxeles por metro, sin sombras horneadas"),
                            ("BAKE", "Con sombras horneadas", "UV únicas + oclusión ambiental real: más realismo, menos píxeles por metro")],
        get=lambda self: 1 if self.bake_ao else 0, set=lambda self, v: setattr(self, "bake_ao", bool(v)),
        description="Cómo se arman las UV y las texturas")
    ba["uv_smart"] = BoolProperty(name="Dar poco espacio a lo tapado", default=True,
                                  description="Analiza qué caras no se ven desde ningún lado (interiores, huecos cerrados) y les da poco lugar en el mapa. Todo lo que se ve tiene la misma nitidez")
    ba["uv_same_density"] = BoolProperty(name="Misma nitidez por material", default=True,
        description="Todo un material (la pared de ladrillo, por ejemplo) se hornea con la misma densidad de píxeles, así no hay zonas borrosas junto a otras nítidas. Las prioridades de zona solo reparten entre materiales distintos")
    ba["uv_grid"] = BoolProperty(name="Mapa UV ordenado", default=True,
        description="Coloca las islas en filas alineadas (atlas modular, como en los diarios de los Region Packs) en vez de rellenar huecos al azar. Con AO horneado igual. Paneles grandes primero, pegados arriba-izquierda; los detalles rellenan huecos")
    ba["uv_group"] = BoolProperty(name="Agrupar islas por material", default=True,
                                  description="Las islas del mismo material quedan juntas en el mapa (colores parecidos juntos, distintos separados), como recomienda la documentación. Cuesta un 4 a 6 % de nitidez")
    for _k, _n, _d in (("wall", "Paredes", "paredes de fachada (todos los materiales de pared)"), ("trim", "Molduras y marcos", "molduras, marcos de ventana, cornisas y losas"),
                       ("roof", "Techo", "el techo (hasta donde dejan los topes de la azotea)"), ("door", "Puertas", "puertas"),
                       ("gar", "Portones", "portones de garaje"), ("rail", "Barandas", "barandas y pasamanos")):
        ba["uv_c_" + _k] = FloatProperty(name=_n, default=1.0, min=0.25, max=3.0, precision=2, subtype="FACTOR",
                                         description=f"Nitidez de {_d}. Más de 1: sus UV ocupan más lugar en la textura y se ven con más píxeles por metro. "
                                                     "Todas las caras del componente quedan con la misma nitidez. Se aplica al regenerar las UV")
    ba["uv_pins"] = StringProperty(name="Zonas fijadas", default="",
                                   description="Caras del LOD0 cuyas UV se conservan al regenerar (datos internos)")
    ba["bake_ao_str"] = FloatProperty(name="Fuerza de la oclusión", default=0.8, min=0.0, max=1.0, subtype="FACTOR")
    ba["bake_dist"] = FloatProperty(name="Distancia de la oclusión (m)", default=2.5, min=0.1, max=20.0)
    ba["bake_samples"] = IntProperty(name="Muestras", default=32, min=1, max=512)
    ba["bake_margin"] = IntProperty(name="Margen (px)", default=8, min=1, max=64)
    ba["bake_share"] = BoolProperty(name="Compartir islas iguales", default=True,
                                    description="Las caras con la misma forma, material y sombra usan el mismo lugar del mapa UV: más píxeles por metro")
    ba["bake_share_loose"] = FloatProperty(name="Tolerancia de sombra", default=0.15, min=0.03, max=0.5, precision=2,
                                           description="Cuánto pueden diferir las sombras de dos caras iguales para compartir isla. Más alto: más islas compartidas y más píxeles por metro, pero la sombra de una puede no ser exacta en la otra")
    ba["bake_share_area"] = FloatProperty(name="Tamaño máximo (m²)", default=12.0, min=1.0, max=200.0, precision=0,
                                          description="Las caras iguales más grandes que esto no comparten isla (cada una tiene su lugar). Más alto: se comparten más caras grandes, hay más píxeles por metro, pero con más riesgo de sombra o ladrillo que no coincida. Más bajo: más seguro y menos nítido")
    ba["bake_roof_wt"] = FloatProperty(name="Nitidez de la azotea", default=1.0, min=0.25, max=1.0, precision=2, subtype="FACTOR",
                                       description="Píxeles por metro de la azotea plana respecto a las paredes (1 = igual). Bajarlo libera espacio del mapa y sube la nitidez del resto, a costa de una azotea más borrosa")
    ba["bake_align_x"] = BoolProperty(name="También en horizontal", default=False,
                                      description="Exige además que las juntas verticales del ladrillo coincidan. Casi siempre se ve igual y comparte muchas menos caras; las hiladas (horizontales) siempre se alinean")
    ba["bake_align"] = BoolProperty(name="Alinear el patrón al compartir", default=True,
                                    description="Dos caras iguales solo comparten isla si el dibujo de la textura (ladrillos, tejas) cae en el mismo punto de su patrón, en vertical y en horizontal: así las hiladas y las juntas quedan continuas entre paneles. Si lo apagas se comparte más, pero los ladrillos se ven corridos")
    ba["pbr_prev_size"] = EnumProperty(name="Calidad de la vista previa", default="1024", update=_on_change,
                                       items=[("512", "512 (rápida)", ""), ("1024", "1024", ""), ("2048", "2048 (nítida, más lenta)", ""),
                                              ("4096", "4096 (como el export)", "")],
                                       description="Resolución del atlas que se ve en Blender (no cambia lo que se exporta)")
    ba["pbr_folder"] = EnumProperty(name="Carpeta", items=_pbr_folder_items,
                                    description="Carpeta de la biblioteca que se muestra: solo se cargan las miniaturas de esa carpeta")
    ba["pbr_library"] = StringProperty(name="Biblioteca de materiales", default="", update=_pbr_lib_changed, subtype="DIR_PATH",
                                       description="Carpeta con sets PBR (por ejemplo AmbientCG: Categoría/Set/..._Color.jpg)")
    ba["tex_share_lods"] = BoolProperty(name="El LOD1 comparte las texturas del principal", default=True,
                                        description="Siempre activo: el LOD1 usa las texturas del principal y se escribe el settings.json (el juego lo exige)")
    ba["tex_share_asset"] = StringProperty(name="Compartir con otro asset", default="",
                                           description="Nombre de la carpeta de otro asset (mismo paquete .cok) cuyas texturas usar en vez de crear nuevas")


_install_matslot_props()


def load_wall_ui(context):
    """Copia a la interfaz los ajustes de la cara activa (o los valores generales si no tiene)."""
    obj = _edit_shape_obj(context)
    if obj is None:
        return
    bm = bmesh.from_edit_mesh(obj.data)
    f = bm.faces.active
    if f is None:
        return
    lays = wall_layers(bm)
    s = context.scene.cs2_building
    w = context.scene.cs2_wall
    fl = f[lays["cs2_flags"]] if "cs2_flags" in lays else 0

    def val(attr, flag, default):
        if flag and (fl & flag) and attr in lays:
            v = f[lays[attr]]
            return v if (v > 0 or attr in ("cs2_w_depth", "cs2_d_depth", "cs2_w_off", "cs2_d_pos", "cs2_d_off", "cs2_b_off", "cs2_k_band", "cs2_k_pt", "cs2_k_mh", "cs2_k_edge")) else default
        return default
    _WALL_LOCK[0] = True
    try:
        w.win_style = wall_style_identifier(f[lays["cs2_w_style"]]) if "cs2_w_style" in lays else "__GLOBAL__"
        w.door_style_w = wall_style_identifier(f[lays["cs2_d_style"]]) if "cs2_d_style" in lays else "__GLOBAL__"
        w.bal_style_w = wall_style_identifier(f[lays["cs2_b_style"]]) if "cs2_b_style" in lays else "__GLOBAL__"
        w.use_win = bool(fl & FLAG_WIN)
        _c = int(f[lays["cs2_w_count"]]) if (fl & FLAG_WIN) and "cs2_w_count" in lays else 0
        w.win_auto = _c == 0
        w.win_count = _c if _c > 0 else (0 if _c < 0 else 3)
        w.win_w = val("cs2_w_w", FLAG_WIN, s.win_w)
        w.win_h = val("cs2_w_h", FLAG_WIN, s.win_h)
        w.win_sill = val("cs2_w_sill", FLAG_WIN, 0.9)
        w.win_depth = val("cs2_w_depth", FLAG_WIN, 0.25)
        w.win_off = val("cs2_w_off", FLAG_WIN, 0.0)
        w.door_mode = str(int(f[lays["cs2_door_mode"]])) if "cs2_door_mode" in lays else "0"
        w.use_door = bool(fl & FLAG_DOOR)
        w.door_pos = val("cs2_d_pos", FLAG_DOOR, 0.5)
        w.door_off = val("cs2_d_off", FLAG_DOOR, 0.0)
        w.bal_off = val("cs2_b_off", FLAG_BAL, 0.0)
        w.door_w = val("cs2_d_w", FLAG_DOOR, max(1.2, s.win_w))
        w.door_h = val("cs2_d_h", FLAG_DOOR, min(2.2, s.floor_height - 0.4))
        w.door_depth = val("cs2_d_depth", FLAG_DOOR, 0.25)
        w.bal_mode = str(int(f[lays["cs2_bal_mode"]])) if "cs2_bal_mode" in lays else "0"
        w.use_bal = bool(fl & FLAG_BAL)
        w.bal_depth = val("cs2_b_depth", FLAG_BAL, 1.2)
        w.bal_w = val("cs2_b_w", FLAG_BAL, s.win_w + 0.8)
        w.bal_rail = val("cs2_b_rail", FLAG_BAL, 0.9)
        w.f_mat = str(int(f[lays["cs2_f_mat"]])) if "cs2_f_mat" in lays else "0"
        w.f_rot = str(int(f[lays["cs2_f_rot"]]) % 4) if "cs2_f_rot" in lays else "0"
        w.cw_mode = str(int(f[lays["cs2_k_mode"]])) if "cs2_k_mode" in lays else "0"
        w.use_cw = bool(fl & FLAG_CW)
        w.cw_mw = val("cs2_k_mw", FLAG_CW, 1.5)
        w.cw_mh = val("cs2_k_mh", FLAG_CW, 0.0)
        w.cw_pt = val("cs2_k_pt", FLAG_CW, 0.06)
        w.cw_pd = val("cs2_k_pd", FLAG_CW, 0.08)
        w.cw_band = val("cs2_k_band", FLAG_CW, 0.45)
        w.cw_edge = val("cs2_k_edge", FLAG_CW, 0.0)
        w.cw_find = val("cs2_k_find", FLAG_CW, 0.35)
        w.cw_fint = val("cs2_k_fint", FLAG_CW, 0.12)
        for k_, a_, d_ in (("cw_bmat", "cs2_k_bmat", "4"), ("cw_mmat", "cs2_k_mmat", "4"), ("cw_fmat", "cs2_k_fmat", "1")):
            setattr(w, k_, str(int(f[lays[a_]]) or int(d_)) if ((fl & FLAG_CW) and a_ in lays) else d_)
        w.cw_finn = int(f[lays["cs2_k_finn"]]) if ((fl & FLAG_CW) and "cs2_k_finn" in lays) else 0
        _cwd = dict(ht=0.0, hd=0.0, patk=0.6, doorw=0.0, baseh=0.0, caph=0.0, capo=0.0, cornw=0.0)
        for k_ in _CW_NEW_F:
            a_ = "cs2_k_" + k_
            setattr(w, "cw_" + k_, float(f[lays[a_]]) if ((fl & FLAG_CW) and a_ in lays) else _cwd[k_])
        _cwi = dict(prof=0, pat=0, patn=3, basemat=4, capmat=4)
        for k_ in _CW_NEW_I:
            a_ = "cs2_k_" + k_
            v_ = int(f[lays[a_]]) if ((fl & FLAG_CW) and a_ in lays) else 0
            v_ = v_ or _cwi[k_]
            if k_ in ("prof", "pat"):
                v_ = int(f[lays[a_]]) if ((fl & FLAG_CW) and a_ in lays) else 0
            setattr(w, "cw_" + k_, v_ if k_ == "patn" else str(v_))
        w.use_pan = bool(fl & FLAG_PAN)
        w.pan_mode = str(int(f[lays["cs2_p_mode"]]) or 2) if (fl & FLAG_PAN) and "cs2_p_mode" in lays else "2"
        w.pan_mat = str(int(f[lays["cs2_p_mat"]]) or 1) if (fl & FLAG_PAN) and "cs2_p_mat" in lays else "1"
        w.pan_pad = val("cs2_p_pad", FLAG_PAN, 0.35)
        w.pan_prob = val("cs2_p_prob", FLAG_PAN, 0.5)
        w.use_cas = bool(fl & FLAG_CAS)
        w.cas_w = val("cs2_c_w", FLAG_CAS, 0.25)
        w.cas_d = val("cs2_c_d", FLAG_CAS, 0.08)
        w.cas_mat = str(int(f[lays["cs2_c_mat"]]) or 1) if (fl & FLAG_CAS) and "cs2_c_mat" in lays else "1"
        w.use_slt = bool(fl & FLAG_SLT)
        w.slt_mode = str(int(f[lays["cs2_s_mode"]]) or 1) if (fl & FLAG_SLT) and "cs2_s_mode" in lays else "1"
        w.slt_gap = val("cs2_s_gap", FLAG_SLT, 0.3)
        w.slt_w = val("cs2_s_w", FLAG_SLT, 0.07)
        w.slt_d = val("cs2_s_d", FLAG_SLT, 0.06)
        w.slt_mat = str(int(f[lays["cs2_s_mat"]]) or 5) if (fl & FLAG_SLT) and "cs2_s_mat" in lays else "5"
        w.use_por = bool(fl & FLAG_POR)
        on = (fl & FLAG_POR) and "cs2_o_on" in lays
        w.por_on = str(int(f[lays["cs2_o_on"]]) or 2) if on else "2"
        for k in PORCH_ENUMS:
            lay_ = lays.get("cs2_o_" + k)
            setattr(w, "por_" + k, str(int(f[lay_]) or PORCH_DEF[k]) if (on and lay_ is not None) else str(PORCH_DEF[k]))
        for k in PORCH_NUMS:
            lay_ = lays.get("cs2_o_" + k)
            setattr(w, "por_" + k, float(f[lay_]) if (on and lay_ is not None) else PORCH_DEF[k])
        for key, g in NEW_GROUPS.items():
            on_ = bool(fl & g["flag"])
            setattr(w, g["use"], on_)
            for k, (_l, _i, d) in g["ints"].items():
                lay_ = lays.get(g["pre"] + k)
                setattr(w, f"{key}_{k}", str(int(f[lay_])) if (on_ and lay_ is not None) else str(d))
            for k, (_l, _lo, _hi, d) in g["floats"].items():
                lay_ = lays.get(g["pre"] + k)
                setattr(w, f"{key}_{k}", float(f[lay_]) if (on_ and lay_ is not None) else d)
    finally:
        _WALL_LOCK[0] = False


_LOCK = {"on": False, "xray": {}}


def set_building_lock(context, on):
    """Mientras se edita la forma, el edificio generado no se puede seleccionar (no se activan los rayos X)."""
    _LOCK["on"] = on
    try:
        s = context.scene.cs2_building
        name = s.building_name or "Residencial01"
        for lod in range(3):
            o = bpy.data.objects.get(f"{name}_LOD{lod}")
            if o is not None:
                o.hide_select = on
        for win in context.window_manager.windows:
            for area in win.screen.areas:
                if area.type != "VIEW_3D":
                    continue
                for sp in area.spaces:
                    if sp.type != "VIEW_3D":
                        continue
                    ptr = sp.as_pointer()
                    if not on and ptr in _LOCK["xray"]:     # por si quedó activado por una versión anterior
                        sp.shading.show_xray = _LOCK["xray"].pop(ptr)
        if not on:
            _LOCK["xray"].clear()
    except Exception as e:
        print("CS2: bloqueo del edificio:", e)


@persistent
def _depsgraph_handler(scene, depsgraph):
    """Al cambiar la cara activa de la forma en modo edición, carga sus ajustes en el panel."""
    try:
        ctx = bpy.context
        obj = _edit_shape_obj(ctx)
        if (obj is not None) != _LOCK["on"]:  # entró o salió de la edición (con el botón o con Tab)
            set_building_lock(ctx, obj is not None)
        s = scene.cs2_building
        want = "FACES" if obj is not None else "ALL"
        if s.edit_scope != want and not _SCOPE_BUSY[0]:     # el alcance siempre refleja el modo (también con Tab)
            _SCOPE_BUSY[0] = True
            try:
                s.edit_scope = want
                if want == "ALL" and _main_obj(s) is not None:
                    _PENDING[0] = True                      # salió con Tab: falta aplicar lo que se tocó en la forma
            finally:
                _SCOPE_BUSY[0] = False
        if obj is None:
            _LAST_ACTIVE["key"] = None
            return
        bm = bmesh.from_edit_mesh(obj.data)
        f = bm.faces.active
        if f is None:
            return
        c = f.calc_center_median()
        key = (obj.name, round(c.x, 2), round(c.y, 2), round(c.z, 2))
        if key != _LAST_ACTIVE["key"]:
            _LAST_ACTIVE["key"] = key
            load_wall_ui(ctx)
    except Exception:
        _log_exc("cambio_cara_activa")


class CS2_OT_edit_shape(bpy.types.Operator):
    bl_idname = "cs2.edit_shape"
    bl_label = "Editar forma / volver"
    bl_description = "Entra al modo edición de la forma (para elegir paredes y alturas) o sale y actualiza el edificio"

    def execute(self, context):
        s = context.scene.cs2_building
        obj = s.shape_object
        if obj is None or obj.type != "MESH":
            self.report({"ERROR"}, "Elegí o creá una forma base primero")
            return {"CANCELLED"}
        if obj.mode == "EDIT":
            context.view_layer.objects.active = obj   # si el objeto activo cambió o se deseleccionó, mode_set falla
            bpy.ops.object.mode_set(mode="OBJECT")
            set_building_lock(context, False)
            s.edit_scope = "ALL"
            safe_regenerate(self, context)
            return {"FINISHED"}
        for o in context.view_layer.objects:
            o.select_set(False)
        obj.hide_set(False)
        obj.select_set(True)
        context.view_layer.objects.active = obj
        bpy.ops.object.mode_set(mode="EDIT")
        context.tool_settings.mesh_select_mode = (False, False, True)  # caras
        set_building_lock(context, True)
        s.edit_scope = "FACES"
        _LAST_ACTIVE["key"] = None
        return {"FINISHED"}


class CS2_OT_floor(bpy.types.Operator):
    bl_idname = "cs2.floor"
    bl_label = "Piso"
    bl_description = "Sube o baja exactamente un piso lo que tengas seleccionado en la forma"
    bl_options = {"REGISTER", "UNDO"}

    direction: EnumProperty(name="Dirección", items=[("UP", "Agregar piso", ""), ("DOWN", "Quitar piso", "")])

    def execute(self, context):
        obj = _edit_shape_obj(context)
        if obj is None:
            self.report({"ERROR"}, "Entrá en modo edición de la forma (botón 'Editar forma')")
            return {"CANCELLED"}
        fh = context.scene.cs2_building.floor_height
        bm = bmesh.from_edit_mesh(obj.data)
        faces = _selected_faces(bm)
        if not faces:
            self.report({"ERROR"}, "Seleccioná una o más caras (por ejemplo la cara de arriba de un volumen)")
            return {"CANCELLED"}
        # la forma puede estar movida o escalada: el desplazamiento se hace en coordenadas del mundo
        inv = obj.matrix_world.to_3x3().inverted()
        if self.direction == "UP":
            res = bmesh.ops.extrude_face_region(bm, geom=faces)
            bmesh.ops.delete(bm, geom=faces, context="FACES_ONLY")
            newv = [g for g in res["geom"] if isinstance(g, bmesh.types.BMVert)]
            bmesh.ops.translate(bm, vec=inv @ Vector((0, 0, fh)), verts=newv)
            newf = list({f for v in newv for f in v.link_faces})  # tapa nueva + paredes nuevas
            bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
            lays = wall_layers(bm)
            newset = set(newf)
            for nf in newf:  # el piso nuevo hereda los ajustes de la pared de abajo
                if abs(nf.normal.z) > 0.5 or not lays:
                    continue
                src_f = None
                for e in nf.edges:
                    for lf in e.link_faces:
                        if lf not in newset and abs(lf.normal.z) < 0.5 and lf.normal.dot(nf.normal) > 0.9:
                            src_f = lf
                            break
                    if src_f:
                        break
                if src_f:
                    for lay in lays.values():
                        nf[lay] = src_f[lay]
            for f in bm.faces:
                f.select_set(False)
            tops = [f for f in newf if f.is_valid and (obj.matrix_world.to_3x3() @ f.normal).z > 0.9] or newf
            for f in tops:
                f.select_set(True)
            bm.faces.active = tops[0] if tops else None
        else:
            verts = {v for f in faces for v in f.verts}
            bmesh.ops.translate(bm, vec=inv @ Vector((0, 0, -fh)), verts=list(verts))
            # si la forma estaba dividida por pisos, el piso quitado queda plano: se une y desaparece
            bmesh.ops.remove_doubles(bm, verts=list(bm.verts), dist=1e-4)
        bmesh.update_edit_mesh(obj.data, loop_triangles=True, destructive=True)
        _on_change(context.scene.cs2_building, context)
        return {"FINISHED"}


class CS2_OT_split_floors(bpy.types.Operator):
    bl_idname = "cs2.split_floors"
    bl_label = "Dividir por pisos"
    bl_description = "Corta las paredes de la forma en cada piso, para poder elegir y editar un piso solo"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        s = context.scene.cs2_building
        obj = s.shape_object
        if obj is None or obj.type != "MESH":
            self.report({"ERROR"}, "Elegí o creá una forma base primero")
            return {"CANCELLED"}
        editing = obj.mode == "EDIT"
        bm = bmesh.from_edit_mesh(obj.data) if editing else bmesh.new()
        if not editing:
            bm.from_mesh(obj.data)
        n0 = len(bm.faces)
        split_floors(bm, s.floor_height / max(obj.matrix_world.to_scale().z, 1e-6), zbase=None)
        if editing:
            bmesh.update_edit_mesh(obj.data, loop_triangles=True, destructive=True)
        else:
            bm.to_mesh(obj.data)
            bm.free()
        self.report({"INFO"}, f"Caras: {n0} -> {len(obj.data.polygons) if not editing else '...'}")
        _on_change(s, context)
        return {"FINISHED"}


class CS2_OT_select_band(bpy.types.Operator):
    bl_idname = "cs2.select_band"
    bl_label = "Seleccionar"
    bl_description = "Extiende la selección a todo el piso o a toda la pared de la cara activa"
    bl_options = {"REGISTER", "UNDO"}

    kind: EnumProperty(name="Qué", items=[("FLOOR", "Piso", "Todas las paredes del mismo piso"),
                                          ("WALL", "Pared", "Todos los pisos de la misma pared")])

    def execute(self, context):
        obj = _edit_shape_obj(context)
        if obj is None:
            return {"CANCELLED"}
        bm = bmesh.from_edit_mesh(obj.data)
        act = bm.faces.active
        if act is None or abs(act.normal.z) > 0.5:
            self.report({"ERROR"}, "Hacé clic primero en una pared")
            return {"CANCELLED"}
        mw = obj.matrix_world
        nrm = (mw.to_3x3() @ act.normal).normalized()
        zc = (mw @ act.calc_center_median()).z
        plane = (mw @ act.calc_center_median()).dot(Vector((nrm.x, nrm.y, 0.0)))
        fh = context.scene.cs2_building.floor_height
        for f in bm.faces:
            if abs(f.normal.z) > 0.5:
                continue
            c = mw @ f.calc_center_median()
            nf = (mw.to_3x3() @ f.normal).normalized()
            if self.kind == "FLOOR":
                if abs(c.z - zc) < fh * 0.5:
                    f.select_set(True)
            elif nf.dot(nrm) > 0.99 and abs(c.dot(Vector((nrm.x, nrm.y, 0.0))) - plane) < 0.02:
                f.select_set(True)
        bmesh.update_edit_mesh(obj.data, loop_triangles=False, destructive=False)
        return {"FINISHED"}


class CS2_OT_wall_reset(bpy.types.Operator):
    bl_idname = "cs2.wall_reset"
    bl_label = "Volver a automático"
    bl_description = "Quita los ajustes propios de las paredes seleccionadas"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        obj = _edit_shape_obj(context)
        if obj is None:
            return {"CANCELLED"}
        bm = bmesh.from_edit_mesh(obj.data)
        lays = wall_layers(bm)
        for f in _selected_faces(bm):
            for name, lay in lays.items():
                f[lay] = 0
        bmesh.update_edit_mesh(obj.data, loop_triangles=False, destructive=False)
        _LAST_ACTIVE["key"] = None
        load_wall_ui(context)
        _on_change(context.scene.cs2_building, context)
        return {"FINISHED"}


def _draw_shape_tools(lay, context, s):
    obj = s.shape_object
    editing = _edit_shape_obj(context) is not None
    box = lay.box()
    box.label(text="Alturas y paredes", icon="MOD_BUILD")
    box.operator("cs2.edit_shape", text="Volver y actualizar" if editing else "Editar forma (paredes y alturas)",
                 icon="LOOP_BACK" if editing else "EDITMODE_HLT")
    box.prop(s, "snap_floors")
    box.operator("cs2.split_floors", icon="MOD_ARRAY")
    if not editing:
        ui_note(box, "Entrá en edición para elegir una pared o un volumen.")
        return
    bm = bmesh.from_edit_mesh(obj.data)
    sel = _selected_faces(bm)
    if sel:
        zs = [(obj.matrix_world @ v.co).z for f in sel for v in f.verts]
        zmin_all = min((obj.matrix_world @ v.co).z for v in bm.verts)
        box.label(text=f"{len(sel)} cara(s): de {(min(zs) - zmin_all) / s.floor_height:.1f} "
                       f"a {(max(zs) - zmin_all) / s.floor_height:.1f} pisos")
    else:
        box.label(text="Seleccioná caras con clic (modo cara).")
    row = box.row(align=True)
    row.operator("cs2.floor", text="+ Piso", icon="TRIA_UP").direction = "UP"
    row.operator("cs2.floor", text="- Piso", icon="TRIA_DOWN").direction = "DOWN"
    box.label(text="+ Piso: extruye la cara de arriba un piso.")
    row = box.row(align=True)
    row.operator("cs2.select_band", text="Todo el piso").kind = "FLOOR"
    row.operator("cs2.select_band", text="Toda la pared").kind = "WALL"

    lay.label(text="Ventanas, puertas, balcones y techo de estas caras:", icon="INFO")
    lay.label(text="se editan en el panel '3 · Personalización'.")


# ----------------------------------------------------------------------------
# Editor de estilos: propiedades, vista previa, catálogo, operadores y panel
# ----------------------------------------------------------------------------
_STYLE_LOCK = [False]


def _make_curtain_object(context, st, name, coll, loc):
    """Un paño de muro cortina (dos pisos) con el estilo puesto: vista previa, catálogo y miniatura."""
    s = context.scene.cs2_building
    p = props_to_params(s)
    fh = max(2.8, float(p.get("floor_h", 3.0)))
    p["floor_h"] = fh
    Lw, Hw = 6.4, fh * 2
    look = dict(p.get("look") or {})
    look.update({k: st[k] for k in CW_STYLE_KEYS if k in st})
    look["cw_on"] = 1
    p["look"] = look
    p["_extra_mats"] = []
    bm = bmesh.new()
    bm.loops.layers.uv.new("UVMap")
    bm.faces.layers.int.new("is_piece")
    pts = [Vector((-Lw / 2, 0.0, 0.0)), Vector((Lw / 2, 0.0, 0.0)), Vector((Lw / 2, 0.0, Hw)), Vector((-Lw / 2, 0.0, Hw))]
    build_curtain(bm, pts, Vector((0, -1, 0)), p, 0, 0, None, look_cfg(p, None))
    finish_bmesh(bm, p)
    mesh = bpy.data.meshes.new(name)
    bm.to_mesh(mesh)
    bm.free()
    for m in get_materials(p) + p["_extra_mats"]:
        mesh.materials.append(m)
    obj = bpy.data.objects.new(name, mesh)
    coll.objects.link(obj)
    obj.location = loc
    return obj, (Lw, Hw)


def make_style_object(context, st, name, coll, loc, w=None, h=None, depth=0.25):
    """Un tramo de pared con una abertura y el estilo puesto (vista previa o catálogo)."""
    s = context.scene.cs2_building
    p = props_to_params(s)
    kind = st.get("kind", "window")
    if kind == "curtain":
        return _make_curtain_object(context, st, name, coll, loc)
    door = kind == "door"
    bal = kind == "balcony"
    w = w or (st.get("size_w") or (1.3 if door else 1.5))
    _sh = st.get("size_h") or 0
    h = h or ((_sh + 0.2) if _sh else (2.3 if door else 1.7))
    zb = 0.0 if door else 0.9
    if bal:
        w, h, zb = 1.3, 2.1, 0.8
    L = w + (3.4 if bal else 2.4)
    H = zb + h + (0.9 if not bal else 0.8)
    ops = [(L / 2 - w / 2, L / 2 + w / 2, zb, zb + h, MAT["door" if door else "glass"])]
    p["window_style"] = None if (door or bal) else st
    p["door_style"] = st if door else None
    p["balcony_style"] = st if bal else None
    p["window_pieces"], p["door_pieces"] = [], []
    p["_extra_mats"] = []
    bm = bmesh.new()
    bm.loops.layers.uv.new("UVMap")
    bm.faces.layers.int.new("is_piece")
    facade_faces(bm, (-L / 2, 0.0), (0, -1, 0), L, H, ops, depth, p, 0)
    if bal:
        insert_balcony(bm, st, Vector((-L / 2, 0.0, 0.0)), Vector((1, 0, 0)), Vector((0, -1, 0)),
                       L / 2, zb, None, None, None, w, p)
    finish_bmesh(bm, p)
    mesh = bpy.data.meshes.new(name)
    bm.to_mesh(mesh)
    bm.free()
    for m in get_materials(p) + p["_extra_mats"]:
        mesh.materials.append(m)
    obj = bpy.data.objects.new(name, mesh)
    coll.objects.link(obj)
    obj.location = loc
    return obj, (L, H)


def _clear_collection(coll):
    for o in list(coll.objects):
        me = o.data
        bpy.data.objects.remove(o, do_unlink=True)
        if me is not None and me.users == 0:
            bpy.data.meshes.remove(me)


def style_from_editor(sp):
    st = {}
    for k, v0 in STYLE_DEFAULT.items():
        if k in CW_STYLE_KEYS:
            continue
        st[k] = getattr(sp, k)
    return clean_style(st)


def editor_from_style(sp, st):
    full = {**STYLE_DEFAULT, **st}
    _STYLE_LOCK[0] = True
    try:
        for k in STYLE_DEFAULT:
            if k in CW_STYLE_KEYS:
                continue
            try:
                setattr(sp, k, full[k])
            except (TypeError, ValueError):
                pass
    finally:
        _STYLE_LOCK[0] = False


def build_preview(context):
    sp = context.scene.cs2_style
    coll = get_collection(context, "CS2_Preview")
    _clear_collection(coll)
    obj, _size = make_style_object(context, style_from_editor(sp), "CS2_Estilo_preview", coll,
                                   context.scene.cursor.location, sp.prev_w, sp.prev_h, sp.prev_depth)
    return obj


def _style_changed(self, context):
    if _STYLE_LOCK[0]:
        return
    _EDITOR["dirty"] = True
    sc = context.scene
    try:
        if self.live_preview:
            build_preview(context)
        if self.apply_live:
            _on_change(sc.cs2_building, context)
    except Exception as e:
        print("CS2: error en la vista previa:", e)


_EDITOR = {"dirty": False, "key": None, "n": 0}
_KIND_PROP = {"window": "window_style", "door": "door_style", "balcony": "balcony_style"}
_KIND_WALL = {"window": "win_style", "door": "door_style_w", "balcony": "bal_style_w"}
_KIND_LABEL = {"window": "ventana", "door": "puerta", "balcony": "balcón", "curtain": "muro cortina"}


def load_editor_for(context, kind):
    """Pone en el editor el estilo que el edificio usa hoy para ese tipo (o uno nuevo)."""
    s = context.scene.cs2_building
    sp = context.scene.cs2_style
    cur = getattr(s, _KIND_PROP[kind])
    st = get_style(cur) if cur not in ("NONE", "") else None
    editor_from_style(sp, {**STYLE_DEFAULT, **(st or {}), "kind": kind})
    sp.save_name = cur if st else f"Mi {_KIND_LABEL[kind]}"
    _EDITOR["dirty"] = False
    try:
        bpy.app.timers.register(_refresh_editor_thumb, first_interval=0.05)
    except Exception:
        _log_exc("load_editor_for")


def _ed_toggle(kind):
    def upd(self, context):
        if _STYLE_LOCK[0] or not getattr(self, "ed_" + kind):
            return
        _STYLE_LOCK[0] = True
        try:
            for k in _KIND_PROP:
                if k != kind:
                    setattr(self, "ed_" + k, False)
        finally:
            _STYLE_LOCK[0] = False
        load_editor_for(context, kind)
    return upd


def _refresh_editor_thumb():
    """Dibuja el estilo del editor (sin guardar) en la miniatura de vista previa."""
    try:
        sp = bpy.context.scene.cs2_style
        d = thumb_dir()
        if not d:
            return None
        path = os.path.join(d, "_editor.png")
        render_thumb(style_from_editor(sp), path)
        pc = _THUMBS["pcoll"]
        if pc is None:
            pc = _THUMBS["pcoll"] = bpy.utils.previews.new()
        _EDITOR["n"] += 1
        key = f"_editor{_EDITOR['n']}"
        pc.load(key, path, "IMAGE", force_reload=True)
        _EDITOR["key"] = key
        _EDITOR["dirty"] = False
        _redraw_all()
    except Exception as e:
        print("CS2: vista previa del editor:", e)
    return None


def _sp(**kw):
    kw["update"] = _style_changed
    return kw


class CS2StyleProps(bpy.types.PropertyGroup):
    kind: EnumProperty(name="Tipo", default="window", items=[("window", "Ventana", ""), ("door", "Puerta", ""), ("balcony", "Balcón", "")],
                       update=_style_changed)
    shape: EnumProperty(name="Forma", default="RECT", items=STYLE_SHAPES, update=_style_changed)
    arch_rise: FloatProperty(name="Altura del arco", default=0.5, min=0.1, max=1.5, update=_style_changed,
                             description="Como fracción del ancho: 0.5 = medio punto, menos = rebajado")
    frame_w: FloatProperty(name="Ancho del marco (m)", default=0.06, min=0.0, max=0.3, update=_style_changed)
    frame_d: FloatProperty(name="Relieve del marco (m)", default=0.06, min=0.0, max=0.2, update=_style_changed)
    cols: IntProperty(name="Divisiones verticales", default=1, min=1, max=8, update=_style_changed)
    rows: IntProperty(name="Divisiones horizontales", default=1, min=1, max=8, update=_style_changed)
    bar_w: FloatProperty(name="Ancho de barra (m)", default=0.035, min=0.01, max=0.12, update=_style_changed)
    bar_d: FloatProperty(name="Relieve de barra (m)", default=0.03, min=0.005, max=0.1, update=_style_changed)
    transom: FloatProperty(name="Travesaño / montante", default=0.0, min=0.0, max=0.95, subtype="FACTOR",
                           update=_style_changed, description="Altura relativa; 0 = sin travesaño")
    lattice: IntProperty(name="Celosía de rombos", default=0, min=0, max=6, update=_style_changed)
    casing_w: FloatProperty(name="Moldura: ancho (m)", default=0.0, min=0.0, max=0.4, update=_style_changed)
    casing_d: FloatProperty(name="Moldura: relieve (m)", default=0.05, min=0.01, max=0.2, update=_style_changed)
    head: EnumProperty(name="Remate", default="NONE", items=STYLE_HEADS, update=_style_changed)
    head_h: FloatProperty(name="Alto del remate (m)", default=0.25, min=0.05, max=0.8, update=_style_changed)
    sill: EnumProperty(name="Repisa", default="NONE", items=STYLE_SILLS, update=_style_changed)
    sill_d: FloatProperty(name="Salida de la repisa (m)", default=0.12, min=0.04, max=0.8, update=_style_changed)
    shutters: EnumProperty(name="Persianas", default="NONE", items=STYLE_SHUTTERS, update=_style_changed)
    louvers: IntProperty(name="Tablillas", default=6, min=0, max=10, update=_style_changed)
    pilasters: BoolProperty(name="Pilastras a los lados", default=False, update=_style_changed)
    pilaster_w: FloatProperty(name="Ancho de pilastra (m)", default=0.12, min=0.04, max=0.4, update=_style_changed)
    leaves: IntProperty(name="Hojas", default=1, min=1, max=2, update=_style_changed)
    leaf_type: EnumProperty(name="Hoja", default="PANELS", items=STYLE_LEAVES, update=_style_changed)
    panels: IntProperty(name="Cuarterones", default=2, min=1, max=4, update=_style_changed)
    lite: EnumProperty(name="Vidrio", default="NONE", items=STYLE_LITES, update=_style_changed)
    lite_n: IntProperty(name="Cantidad de vidrios", default=3, min=1, max=6, update=_style_changed)
    glass_frac: FloatProperty(name="Proporción de vidrio", default=0.4, min=0.1, max=0.9, subtype="FACTOR",
                              update=_style_changed)
    sidelights: FloatProperty(name="Laterales vidriados (m)", default=0.0, min=0.0, max=1.0, update=_style_changed)
    handle: BoolProperty(name="Manija", default=True, update=_style_changed)
    garage: BoolProperty(name="Portón de garaje (usa el material de portones)", default=False, update=_style_changed)
    size_w: FloatProperty(name="Ancho propio (m, 0 = general)", default=0.0, min=0.0, max=8.0, update=_style_changed)
    size_h: FloatProperty(name="Alto propio (m, 0 = general)", default=0.0, min=0.0, max=4.0, update=_style_changed)
    bal_type: EnumProperty(name="Tipo de balcón", default="SLAB", items=STYLE_BAL_TYPES, update=_style_changed)
    bal_front: EnumProperty(name="Frente", default="RECT", items=STYLE_BAL_FRONTS, update=_style_changed)
    bal_depth: FloatProperty(name="Salida (m)", default=1.2, min=0.3, max=3.0, update=_style_changed)
    bal_extra_w: FloatProperty(name="Ancho extra sobre la ventana (m)", default=0.8, min=0.0, max=4.0,
                               update=_style_changed)
    bal_slab_t: FloatProperty(name="Espesor de la losa (m)", default=0.18, min=0.06, max=0.4, update=_style_changed)
    bal_support: EnumProperty(name="Apoyo", default="NONE", items=STYLE_BAL_SUPPORTS, update=_style_changed)
    bal_rail: EnumProperty(name="Baranda", default="BALUSTERS", items=STYLE_BAL_RAILS, update=_style_changed)
    bal_rail_h: FloatProperty(name="Alto de baranda (m)", default=0.95, min=0.3, max=1.5, update=_style_changed)
    bal_bar_gap: FloatProperty(name="Separación de barrotes (m)", default=0.14, min=0.05, max=0.6,
                               update=_style_changed)
    bal_sides: BoolProperty(name="Baranda en los costados", default=True, update=_style_changed)
    bal_cap: BoolProperty(name="Pasamanos", default=True, update=_style_changed)
    bal_bar_r: FloatProperty(name="Grosor de barrotes (m)", default=0.014, min=0.004, max=0.06, precision=3,
                             update=_style_changed)
    bal_post_r: FloatProperty(name="Grosor de postes (m)", default=0.028, min=0.008, max=0.12, precision=3,
                              update=_style_changed)
    bal_cap_w: FloatProperty(name="Ancho del pasamanos (m)", default=0.09, min=0.03, max=0.3, update=_style_changed)
    bal_cap_h: FloatProperty(name="Alto del pasamanos (m)", default=0.05, min=0.02, max=0.2, update=_style_changed)
    bal_roof: EnumProperty(name="Techo", default="NONE", items=STYLE_BAL_ROOFS, update=_style_changed)
    bal_planter: BoolProperty(name="Macetero", default=False, update=_style_changed)
    bal_run: BoolProperty(name="Corrido (une los balcones del piso)", default=False, update=_style_changed,
                          description="Una sola losa y baranda a lo largo de toda la fila de balcones de un piso; en una curva sigue el arco")
    bal_planter_h: FloatProperty(name="Alto del macetero (m)", default=0.3, min=0.1, max=0.8, update=_style_changed)
    bal_bay_h: FloatProperty(name="Alto del mirador (m)", default=2.2, min=1.8, max=3.2, update=_style_changed)
    ed_window: BoolProperty(name="Editar estilo de ventanas", default=False, update=_ed_toggle("window"))
    ed_door: BoolProperty(name="Editar estilo de puertas", default=False, update=_ed_toggle("door"))
    ed_balcony: BoolProperty(name="Editar estilo de balcones", default=False, update=_ed_toggle("balcony"))
    # --- herramientas ---
    pick: EnumProperty(name="Estilo", items=_items_pick)
    save_name: StringProperty(name="Nombre", default="Mi estilo")
    live_preview: BoolProperty(name="Vista previa en el cursor 3D", default=False, update=_style_changed)
    apply_live: BoolProperty(name="Probar en el edificio", default=False, update=_style_changed,
                             description="Aplica este estilo, sin guardarlo, a todas las ventanas (o puertas) del edificio")
    prev_w: FloatProperty(name="Ancho de prueba (m)", default=1.5, min=0.5, max=4.0, update=_style_changed)
    prev_h: FloatProperty(name="Alto de prueba (m)", default=1.7, min=0.8, max=4.0, update=_style_changed)
    prev_depth: FloatProperty(name="Profundidad de prueba (m)", default=0.25, min=0.0, max=0.8,
                              update=_style_changed)


class CS2_OT_style_load(bpy.types.Operator):
    bl_idname = "cs2.style_load"
    bl_label = "Cargar en el editor"
    bl_description = "Copia el estilo elegido al editor para modificarlo"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        sp = context.scene.cs2_style
        st = get_style(sp.pick)
        if st is None:
            self.report({"ERROR"}, "Elegí un estilo de la lista")
            return {"CANCELLED"}
        editor_from_style(sp, st)
        sp.save_name = sp.pick
        _style_changed(sp, context)
        return {"FINISHED"}


class CS2_OT_style_save(bpy.types.Operator):
    bl_idname = "cs2.style_save"
    bl_label = "Guardar en mi biblioteca"
    bl_description = "Guarda el estilo del editor para usarlo en cualquier proyecto de este Blender"

    def execute(self, context):
        sp = context.scene.cs2_style
        name = sp.save_name.strip()
        if not name:
            self.report({"ERROR"}, "Escribí un nombre")
            return {"CANCELLED"}
        try:
            path = save_user_style(name, style_from_editor(sp))
        except Exception as e:
            log_error(getattr(self, "bl_idname", "operador"), e)
            self.report({"ERROR"}, str(e))
            return {"CANCELLED"}
        sp.pick = name
        self.report({"INFO"}, f"Guardado: {path}")
        return {"FINISHED"}


class CS2_OT_style_delete(bpy.types.Operator):
    bl_idname = "cs2.style_delete"
    bl_label = "Borrar de mi biblioteca"
    bl_description = "Borra el estilo elegido (solo los tuyos, marcados con estrella)"

    def execute(self, context):
        sp = context.scene.cs2_style
        if sp.pick not in _LIB["user"]:
            self.report({"ERROR"}, "Solo se pueden borrar tus propios estilos (los marcados con ★)")
            return {"CANCELLED"}
        delete_user_style(sp.pick)
        return {"FINISHED"}


class CS2_OT_style_use(bpy.types.Operator):
    bl_idname = "cs2.style_use"
    bl_label = "Usar en todo el edificio"
    bl_description = "Guarda el estilo del editor y lo usa para todas las ventanas (o puertas) del edificio"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        sp = context.scene.cs2_style
        s = context.scene.cs2_building
        name = sp.save_name.strip()
        if not name:
            self.report({"ERROR"}, "Escribí un nombre")
            return {"CANCELLED"}
        try:
            save_user_style(name, style_from_editor(sp))
        except Exception as e:
            log_error(getattr(self, "bl_idname", "operador"), e)
            self.report({"ERROR"}, str(e))
            return {"CANCELLED"}
        sp.pick = name
        if sp.kind == "door":
            s.door_style = name
        elif sp.kind == "balcony":
            s.balcony_style = name
        else:
            s.window_style = name
        sp.apply_live = False
        return {"FINISHED"}


class CS2_OT_style_preview(bpy.types.Operator):
    bl_idname = "cs2.style_preview"
    bl_label = "Ver en el cursor 3D"
    bl_description = "Crea o actualiza una vista previa del estilo en la posición del cursor 3D"

    def execute(self, context):
        build_preview(context)
        return {"FINISHED"}


class CS2_OT_style_catalog(bpy.types.Operator):
    bl_idname = "cs2.style_catalog"
    bl_label = "Crear catálogo"
    bl_description = "Dibuja todos los estilos en una grilla (en el cursor 3D) para ver y elegir"
    bl_options = {"REGISTER", "UNDO"}

    kind: EnumProperty(name="Mostrar", default="all",
                       items=[("all", "Todos", ""), ("window", "Ventanas", ""), ("door", "Puertas", ""),
                              ("balcony", "Balcones", ""), ("curtain", "Muros cortina", "")])

    def execute(self, context):
        lib_ensure()
        coll = get_collection(context, "CS2_Catalogo")
        _clear_collection(coll)
        names = [n for n in _LIB["names"] if self.kind == "all" or _LIB["styles"][n]["kind"] == self.kind]
        cols = 10
        cur = Vector(context.scene.cursor.location)
        sx, sy = 4.4, 4.6
        nrows = (len(names) + cols - 1) // cols
        for i, name in enumerate(names):
            r, c = divmod(i, cols)
            make_style_object(context, _LIB["styles"][name], f"Cat_{i + 1:03d}_{name}", coll,
                              cur + Vector((c * sx, 0.0, (nrows - 1 - r) * sy)))
        self.report({"INFO"}, f"{len(names)} estilos en la colección CS2_Catalogo")
        return {"FINISHED"}


class CS2_OT_style_export(bpy.types.Operator, ExportHelper):
    bl_idname = "cs2.style_export"
    bl_label = "Exportar mis estilos"
    bl_description = "Guarda todos tus estilos en un archivo para llevarlos a otra computadora"
    filename_ext = ".json"
    filter_glob: StringProperty(default="*.json", options={"HIDDEN"})

    def execute(self, context):
        lib_ensure()
        data = {"app": "cs2_edificio", "version": 1,
                "styles": [{"name": n, "style": clean_style(_LIB["styles"][n])} for n in sorted(_LIB["user"])]}
        with open(self.filepath, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=1)
        self.report({"INFO"}, f"{len(data['styles'])} estilos exportados")
        return {"FINISHED"}


class CS2_OT_style_import(bpy.types.Operator, ImportHelper):
    bl_idname = "cs2.style_import"
    bl_label = "Importar estilos"
    bl_description = "Agrega a tu biblioteca los estilos de un archivo exportado"
    filename_ext = ".json"
    filter_glob: StringProperty(default="*.json", options={"HIDDEN"})

    def execute(self, context):
        entries = _read_style_file(self.filepath)
        if not entries:
            self.report({"ERROR"}, "El archivo no tiene estilos válidos")
            return {"CANCELLED"}
        for name, st in entries:
            save_user_style(name, st)
        self.report({"INFO"}, f"{len(entries)} estilos importados")
        return {"FINISHED"}


# ----------------------------------------------------------------------------
# Miniaturas de estilos (vista previa al elegir) y carpeta de la biblioteca
# ----------------------------------------------------------------------------
import bpy.utils.previews

THUMB_VER = 1
_THUMBS = {"pcoll": None, "queue": [], "failed": set(), "icons": {}}

STYLE_DOC = {
    "kind": "window | door | balcony | curtain  (qué tipo de estilo es)",
    "shape": "Forma de la abertura: RECT ARCH POINTED CIRCLE OVAL OCTAGON DIAMOND",
    "arch_rise": "Altura del arco como fracción del ancho (0.5 = medio punto)",
    "frame_w": "Ancho del marco (m)", "frame_d": "Relieve del marco (m)",
    "cols": "Divisiones verticales", "rows": "Divisiones horizontales",
    "bar_w": "Ancho de las barras (m)", "bar_d": "Relieve de las barras (m)",
    "transom": "Altura relativa del travesaño (0 = sin travesaño)",
    "lattice": "Celosía de rombos (0 = no, 1 a 6 = cantidad)",
    "casing_w": "Ancho de la moldura (m, 0 = sin moldura)", "casing_d": "Relieve de la moldura (m)",
    "head": "Remate superior: NONE LINTEL CORNICE PEDIMENT KEYSTONE", "head_h": "Alto del remate (m)",
    "sill": "Repisa: NONE SIMPLE CORBEL BALCONETTE", "sill_d": "Salida de la repisa (m)",
    "shutters": "Persianas: NONE OPEN CLOSED", "louvers": "Cantidad de tablillas",
    "pilasters": "Pilastras a los lados (true/false)", "pilaster_w": "Ancho de pilastra (m)",
    "leaves": "Hojas de la puerta (1 o 2)",
    "leaf_type": "Tipo de hoja: FLUSH PANELS PLANK BRACED SLATS LOUVER", "panels": "Cuarterones",
    "lite": "Vidrio de la puerta: NONE TOP FULL OVAL ROUND SLOTS GRID", "lite_n": "Cantidad de vidrios",
    "glass_frac": "Proporción de vidrio (lite = TOP)", "sidelights": "Ancho de laterales vidriados (m)",
    "handle": "Manija (true/false)",
    "bal_type": "Balcón: SLAB (volado) o BAY (mirador vidriado)",
    "bal_front": "Forma del frente: RECT CHAMFER ROUNDED CURVED",
    "bal_depth": "Cuánto sale de la pared (m)", "bal_extra_w": "Ancho extra sobre el de la ventana (m)",
    "bal_slab_t": "Espesor de la losa (m)",
    "bal_support": "Apoyo bajo la losa: NONE CORBELS BRACKETS BEAM",
    "bal_rail": "Baranda: NONE SOLID BALUSTERS RODS GLASS LATTICE WROUGHT",
    "bal_rail_h": "Alto de la baranda (m)", "bal_bar_gap": "Separación entre barrotes (m)",
    "bal_sides": "Baranda en los costados (true/false)", "bal_cap": "Pasamanos (true/false)",
    "bal_bar_r": "Grosor (radio) de los barrotes (m)", "bal_post_r": "Grosor (radio) de los postes de esquina y extremo (m)",
    "bal_cap_w": "Ancho del pasamanos (m)", "bal_cap_h": "Alto del pasamanos (m)",
    "bal_roof": "Techo: NONE AWNING FLAT", "bal_planter": "Macetero (true/false)", "bal_run": "Corrido: une los balcones del piso (true/false)",
    "bal_planter_h": "Alto del macetero (m)", "bal_bay_h": "Alto del mirador (m)",
    "cw_mw": "Muro cortina: ancho del módulo (m)", "cw_mh": "Muro cortina: alto del módulo (0 = uno por piso)",
    "cw_pt": "Grosor de los perfiles (m)", "cw_pd": "Saliente de los perfiles (m)",
    "cw_ht": "Grosor del perfil horizontal (0 = igual al vertical)", "cw_hd": "Saliente del perfil horizontal (0 = igual)",
    "cw_prof": "Forma de perfiles: 0 cuadrado, 1 con tapa plana, 2 redondo, 3 sin perfiles",
    "cw_mmat": "Material de perfiles (1 molduras, 2 fachada, 3 B, 4 C, 5 D)",
    "cw_pat": "Patrón: 0 iguales, 1 angosto y ancho alternados, 2 paño lleno cada N",
    "cw_patn": "Un paño lleno cada N módulos (patrón 2)", "cw_patk": "Ancho del módulo angosto (patrón 1)",
    "cw_band": "Banda opaca por piso (m)", "cw_bmat": "Material de la banda", "cw_edge": "Margen opaco a los lados (m)",
    "cw_finn": "Aleta cada N módulos (0 = sin aletas)", "cw_find": "Saliente de aletas (m)", "cw_fint": "Grosor de aletas (m)",
    "cw_fmat": "Material de aletas", "cw_doorw": "Ancho de la puerta de acceso (0 = sin puerta)",
    "cw_baseh": "Alto del zócalo (m)", "cw_basemat": "Material del zócalo", "cw_caph": "Alto del remate superior (m)",
    "cw_capo": "Saliente del remate (m)", "cw_capmat": "Material del remate", "cw_cornw": "Ancho del perfil de esquina (m)",
}


def write_readme(d):
    path = os.path.join(d, "LEEME_estilos.txt")
    if os.path.exists(path):
        return
    lines = [
        "ESTILOS DE VENTANAS, PUERTAS, BALCONES Y MUROS CORTINA (CS2 Edificio)",
        "",
        "Cada archivo .json de esta carpeta es un estilo tuyo (o un paquete de varios).",
        "Podés editarlos con cualquier editor de texto. Después, en Blender: panel CS2 >",
        "Estilos > 'Recargar biblioteca'. Las miniaturas se regeneran solas.",
        "",
        "Formato de un estilo:",
        '  {"app": "cs2_edificio", "version": 1, "name": "Mi balcón",',
        '   "style": {"kind": "balcony", "bal_rail": "GLASS", "bal_depth": 1.5}}',
        "",
        "Las claves que no pongas toman el valor por defecto. Un archivo con 'styles': [...]",
        "(lista de {name, style}) agrupa varios estilos. Si un nombre coincide con uno incluido,",
        "el tuyo lo reemplaza. Las medidas están en metros.",
        "",
        "Claves disponibles (valor por defecto entre corchetes):",
    ]
    for k, v in STYLE_DEFAULT.items():
        lines.append(f"  {k} [{v}]  -  {STYLE_DOC.get(k, '')}")
    try:
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")
    except OSError:
        pass


def thumb_dir():
    try:
        return bpy.utils.user_resource("CONFIG", path=os.path.join("cs2_estilos", "_miniaturas"), create=True)
    except Exception:
        return None


def thumb_path(name, st):
    d = thumb_dir()
    if not d:
        return None
    h = zlib.crc32(json.dumps(clean_style(st), sort_keys=True).encode("utf-8")) & 0xFFFFFFFF
    return os.path.join(d, f"v{THUMB_VER}_{style_id(name):x}_{h:08x}.png")


def render_thumb(st, path, size=176):
    """Dibuja el estilo sobre un tramo de pared en una escena temporal y guarda el PNG."""
    tmp = bpy.data.scenes.new("CS2_thumb")
    coll = bpy.data.collections.new("CS2_thumb_c")
    tmp.collection.children.link(coll)
    made = []
    try:
        obj, (L, H) = make_style_object(bpy.context, st, "cs2_thumb", coll, (0.0, 0.0, 0.0))
        made.append(obj)
        bal = st.get("kind") == "balcony"
        cam = bpy.data.cameras.new("cs2_thumb_cam")
        cam.type = "ORTHO"
        co = bpy.data.objects.new("cs2_thumb_cam", cam)
        tmp.collection.objects.link(co)
        target = Vector((0.0, -0.6 if bal else 0.0, H / 2 + (0.1 if bal else 0.0)))
        if bal:
            el, az = math.radians(62), math.radians(-24)
            dirv = Vector((-math.sin(az) * math.sin(el), -math.cos(az) * math.sin(el), math.cos(el)))
            cam.ortho_scale = max(L * 0.9, H) * 1.0
        else:
            dirv = Vector((0.0, -1.0, 0.0))
            cam.ortho_scale = max(L, H) * 0.85
        co.location = target + dirv * 40.0
        co.rotation_euler = (target - co.location).to_track_quat("-Z", "Y").to_euler()
        cam.clip_end = 200.0
        tmp.camera = co
        tmp.render.engine = "BLENDER_WORKBENCH"
        tmp.display.shading.light = "STUDIO"
        tmp.display.shading.color_type = "MATERIAL"
        tmp.render.resolution_x = tmp.render.resolution_y = size
        tmp.render.resolution_percentage = 100
        w = bpy.data.worlds.new("cs2_thumb_w")
        w.color = (0.20, 0.21, 0.23)
        tmp.world = w
        tmp.render.filepath = path
        tmp.render.image_settings.file_format = "PNG"
        bpy.ops.render.render(write_still=True, scene=tmp.name)
    finally:
        for o in list(coll.objects):
            me = o.data
            bpy.data.objects.remove(o, do_unlink=True)
            if me is not None and getattr(me, "users", 1) == 0 and hasattr(bpy.data.meshes, "remove") \
                    and me.name in bpy.data.meshes:
                bpy.data.meshes.remove(me)
        for o in [o for o in tmp.collection.objects]:
            data = o.data
            bpy.data.objects.remove(o, do_unlink=True)
            if data is not None and data.name in bpy.data.cameras:
                bpy.data.cameras.remove(data)
        bpy.data.collections.remove(coll)
        wd = tmp.world
        bpy.data.scenes.remove(tmp)
        if wd is not None:
            bpy.data.worlds.remove(wd)


def _redraw_all():
    try:
        for w in bpy.context.window_manager.windows:
            for a in w.screen.areas:
                a.tag_redraw()
    except Exception:
        pass


def _thumb_tick():
    q = _THUMBS["queue"]
    if not q:
        return None
    name = q.pop(0)
    st = get_style(name)
    if st is not None:
        path = thumb_path(name, st)
        if path and not os.path.exists(path):
            try:
                render_thumb(st, path)
            except Exception as e:
                _THUMBS["failed"].add(name)
                print("CS2: no se pudo crear la miniatura de", name, e)
    _redraw_all()
    return 0.05 if q else None


def queue_thumb(name):
    if name in _THUMBS["queue"] or name in _THUMBS["failed"]:
        return
    _THUMBS["queue"].append(name)
    try:
        if not bpy.app.timers.is_registered(_thumb_tick):
            bpy.app.timers.register(_thumb_tick, first_interval=0.05)
    except Exception:
        _log_exc("queue_thumb")


def thumb_icon(name, allow_queue=True):
    """icon_id de la miniatura del estilo (0 si todavía no está; se encarga de crearla)."""
    ic = _THUMBS["icons"].get(name)
    if ic:
        return ic
    st = get_style(name)
    if st is None:
        return 0
    path = thumb_path(name, st)
    if not path:
        return 0
    pc = _THUMBS["pcoll"]
    if pc is None:
        pc = _THUMBS["pcoll"] = bpy.utils.previews.new()
    key = os.path.basename(path)
    if key in pc:
        _THUMBS["icons"][name] = pc[key].icon_id
        return pc[key].icon_id
    if os.path.exists(path):
        try:
            pc.load(key, path, "IMAGE")
            _THUMBS["icons"][name] = pc[key].icon_id
            return pc[key].icon_id
        except Exception:
            _log_exc("icono_miniatura")
            return 0
    if allow_queue:
        queue_thumb(name)
    return 0


def draw_style_picker(lay, data, prop, scope, thumb=True):
    """Lista desplegable con flechas para recorrer los estilos y su miniatura debajo."""
    col = lay.column(align=True)
    cur = getattr(data, prop)
    if thumb:
        col.template_icon_view(data, prop, show_labels=True, scale=6.5, scale_popup=5.0)
    row = col.row(align=True)
    op = row.operator("cs2.style_step", text="", icon="TRIA_LEFT")
    op.scope, op.prop, op.step = scope, prop, -1
    row.prop(data, prop, text="")
    op = row.operator("cs2.style_step", text="", icon="TRIA_RIGHT")
    op.scope, op.prop, op.step = scope, prop, 1
    if thumb and cur not in ("NONE", "__GLOBAL__", "__NONE__", "") and not thumb_icon(cur):
        col.label(text="Preparando vista previa…", icon="TIME")


class CS2_OT_style_step(bpy.types.Operator):
    bl_idname = "cs2.style_step"
    bl_label = "Estilo anterior / siguiente"
    bl_description = "Recorre la lista de estilos"
    bl_options = {"INTERNAL"}

    scope: StringProperty()
    prop: StringProperty()
    step: IntProperty(default=1)

    def execute(self, context):
        data = context.scene.cs2_building if self.scope == "building" else context.scene.cs2_wall
        ids = [it.identifier for it in data.bl_rna.properties[self.prop].enum_items]
        if not ids:
            return {"CANCELLED"}
        cur = getattr(data, self.prop)
        i = ids.index(cur) if cur in ids else 0
        setattr(data, self.prop, ids[(i + self.step) % len(ids)])
        return {"FINISHED"}


class CS2_OT_style_folder(bpy.types.Operator):
    bl_idname = "cs2.style_folder"
    bl_label = "Abrir carpeta de estilos"
    bl_description = "Abre la carpeta donde se guardan tus estilos (.json) para editarlos a mano"

    def execute(self, context):
        d = style_dir()
        if not d:
            self.report({"ERROR"}, "No se encontró la carpeta de usuario de Blender")
            return {"CANCELLED"}
        write_readme(d)
        try:
            bpy.ops.wm.path_open(filepath=d)
        except Exception:
            _log_exc("execute")
        self.report({"INFO"}, d)
        return {"FINISHED"}


class CS2_OT_style_reload(bpy.types.Operator):
    bl_idname = "cs2.style_reload"
    bl_label = "Recargar biblioteca"
    bl_description = "Vuelve a leer los .json de la carpeta (después de editarlos a mano)"

    def execute(self, context):
        lib_refresh()
        _THUMBS["failed"].clear()
        self.report({"INFO"}, f"{len(_LIB['user'])} estilos tuyos, {len(_LIB['names'])} en total")
        return {"FINISHED"}


class CS2_OT_thumbs_all(bpy.types.Operator):
    bl_idname = "cs2.thumbs_all"
    bl_label = "Crear todas las miniaturas"
    bl_description = "Prepara en segundo plano la vista previa de todos los estilos"

    def execute(self, context):
        lib_ensure()
        for n in _LIB["names"]:
            thumb_icon(n)
        self.report({"INFO"}, "Preparando miniaturas…")
        return {"FINISHED"}


def _draw_opening_editor(box, sp, door):
    sub = box.box()
    sub.label(text="Forma y marco")
    sub.prop(sp, "shape")
    if sp.shape in ("ARCH", "POINTED"):
        sub.prop(sp, "arch_rise")
    sub.prop(sp, "frame_w")
    sub.prop(sp, "frame_d")

    sub = box.box()
    sub.label(text="Divisiones")
    sub.prop(sp, "cols")
    sub.prop(sp, "rows")
    sub.prop(sp, "bar_w")
    sub.prop(sp, "bar_d")
    sub.prop(sp, "transom")
    if not door:
        sub.prop(sp, "lattice")

    if door:
        sub = box.box()
        sub.label(text="Hoja y vidrios")
        sub.prop(sp, "leaves")
        sub.prop(sp, "leaf_type")
        if sp.leaf_type in ("PANELS", "SECTIONAL"):
            sub.prop(sp, "panels")
        sub.prop(sp, "lite")
        if sp.lite in ("SLOTS", "GRID"):
            sub.prop(sp, "lite_n")
        if sp.lite == "TOP":
            sub.prop(sp, "glass_frac")
        sub.prop(sp, "sidelights")
        sub.prop(sp, "handle")
        sub.prop(sp, "garage")
        sub.prop(sp, "size_w")
        sub.prop(sp, "size_h")

    sub = box.box()
    sub.label(text="Moldura y remates")
    sub.prop(sp, "casing_w")
    sub.prop(sp, "casing_d")
    sub.prop(sp, "head")
    if sp.head != "NONE":
        sub.prop(sp, "head_h")
    sub.prop(sp, "pilasters")
    if sp.pilasters:
        sub.prop(sp, "pilaster_w")
    if not door:
        sub.prop(sp, "sill")
        if sp.sill != "NONE":
            sub.prop(sp, "sill_d")
        sub.prop(sp, "shutters")
        if sp.shutters != "NONE":
            sub.prop(sp, "louvers")


def _draw_balcony_editor(box, sp):
    sub = box.box()
    sub.label(text="Planta y losa")
    sub.prop(sp, "bal_type")
    sub.prop(sp, "bal_front")
    sub.prop(sp, "bal_run")
    sub.prop(sp, "bal_depth")
    sub.prop(sp, "bal_extra_w")
    sub.prop(sp, "bal_slab_t")
    sub.prop(sp, "bal_support")
    if sp.bal_type == "BAY":
        sub.prop(sp, "bal_bay_h")
    sub = box.box()
    sub.label(text="Baranda")
    sub.prop(sp, "bal_rail")
    if sp.bal_rail != "NONE":
        sub.prop(sp, "bal_rail_h")
        if sp.bal_rail in ("BALUSTERS", "LATTICE", "WROUGHT"):
            sub.prop(sp, "bal_bar_gap")
        sub.prop(sp, "bal_sides")
        sub.prop(sp, "bal_cap")
        if sp.bal_cap:
            sub.prop(sp, "bal_cap_w")
            sub.prop(sp, "bal_cap_h")
        if sp.bal_rail in ("BALUSTERS", "RODS", "LATTICE", "WROUGHT"):
            sub.prop(sp, "bal_bar_r")
            sub.prop(sp, "bal_post_r")
        sub.prop(sp, "bal_planter")
        if sp.bal_planter:
            sub.prop(sp, "bal_planter_h")
    sub = box.box()
    sub.label(text="Techo")
    sub.prop(sp, "bal_roof")


def _draw_editor(box, s, sp, kind):
    """Editor avanzado, desplegado debajo de cada opción de estilo."""
    prop = "ed_" + kind
    if not s.studio_adv:
        return          # el editor detallado de estilos es de la vista avanzada (interruptor «Avanzado» de CS2 Studio)
    if not getattr(sp, prop):
        box.prop(sp, prop, text="Editar este estilo", icon="MODIFIER", toggle=True)
        return
    ed = box.box()
    row = ed.row()
    row.prop(sp, prop, text=f"Editor de {_KIND_LABEL[kind]}", icon="MODIFIER", toggle=True)
    pc = _THUMBS["pcoll"]
    if _EDITOR["key"] and pc is not None and _EDITOR["key"] in pc:
        ed.template_icon(icon_value=pc[_EDITOR["key"]].icon_id, scale=5.5)
    row = ed.row(align=True)
    row.alert = _EDITOR["dirty"]
    row.operator("cs2.style_refresh", icon="FILE_REFRESH",
                 text="Actualizar vista previa" + (" (hay cambios)" if _EDITOR["dirty"] else ""))
    if kind == "balcony":
        _draw_balcony_editor(ed, sp)
    else:
        _draw_opening_editor(ed, sp, kind == "door")
    sub = ed.box()
    sub.label(text="Guardar y aplicar", icon="FILE_TICK")
    sub.prop(sp, "save_name", text="Nombre")
    row = sub.row(align=True)
    row.operator("cs2.style_apply", text="A todo el edificio", icon="HOME").target = "ALL"
    r2 = row.row(align=True)
    r2.enabled = _edit_shape_obj(bpy.context) is not None
    r2.operator("cs2.style_apply", text="A las caras elegidas", icon="SNAP_FACE").target = "FACES"
    row = sub.row(align=True)
    row.operator("cs2.style_save", text="Solo guardar", icon="FILE_TICK")
    row.operator("cs2.style_delete", text="Borrar", icon="TRASH")


class CS2_OT_style_refresh(bpy.types.Operator):
    bl_idname = "cs2.style_refresh"
    bl_label = "Actualizar vista previa"
    bl_description = "Redibuja la miniatura con los ajustes actuales del editor (no toca el edificio)"

    def execute(self, context):
        _refresh_editor_thumb()
        return {"FINISHED"}


class CS2_OT_style_apply(bpy.types.Operator):
    bl_idname = "cs2.style_apply"
    bl_label = "Aplicar"
    bl_description = "Guarda el estilo del editor con ese nombre y lo usa en el edificio"
    bl_options = {"REGISTER", "UNDO"}

    target: EnumProperty(name="Dónde", default="ALL",
                         items=[("ALL", "Todo el edificio", "Todas las ventanas / puertas / balcones"),
                                ("FACES", "Caras seleccionadas", "Solo las paredes elegidas en la forma")])

    def execute(self, context):
        sp = context.scene.cs2_style
        s = context.scene.cs2_building
        name = sp.save_name.strip()
        if not name:
            self.report({"ERROR"}, "Escribí un nombre")
            return {"CANCELLED"}
        kind = sp.kind
        if self.target == "FACES":
            obj = _edit_shape_obj(context)
            if obj is None or not _selected_faces(bmesh.from_edit_mesh(obj.data)):
                self.report({"ERROR"}, "Entrá en 'Editar forma' y elegí una o más paredes")
                return {"CANCELLED"}
        try:
            save_user_style(name, style_from_editor(sp))
        except Exception as e:
            log_error(getattr(self, "bl_idname", "operador"), e)
            self.report({"ERROR"}, str(e))
            return {"CANCELLED"}
        if self.target == "FACES":
            setattr(context.scene.cs2_wall, _KIND_WALL[kind], name)
        else:
            setattr(s, _KIND_PROP[kind], name)
        sp.apply_live = False
        queue_thumb(name)
        self.report({"INFO"}, f"'{name}' aplicado a " + ("las caras elegidas" if self.target == "FACES" else "todo el edificio"))
        return {"FINISHED"}


class CS2_OT_generate(bpy.types.Operator):
    bl_idname = "cs2.generate"
    bl_label = "Crear el edificio"
    bl_description = "Crea o reconstruye la malla principal del edificio (los LODs se generan después, en Exportar). En edificios grandes muestra el avance y se puede cancelar (Esc)"
    bl_options = {"REGISTER", "UNDO"}

    _timer = None
    _gen = None

    def _report_done(self, objs):
        tris = [mesh_tris(o) for o in objs]
        if len(tris) < 3:
            self.report({"INFO"}, f"Malla principal lista: {tris[0]} triángulos. Los LODs se generan en Exportar (o al armar las UV)")
            return
        bad = [t for t, ok in lod_report(objs) if not ok]
        self.report({"WARNING" if bad else "INFO"},
                    f"Triángulos (alto / medio / bajo): {tris[0]} / {tris[1]} / {tris[2]}" + (f" — pasa los límites: {bad[0]}" if bad else ""))

    def execute(self, context):   # sin interfaz (scripts, pruebas): corre todo de una vez
        objs = safe_regenerate(self, context)
        if objs is None:
            return {"CANCELLED"}
        self._report_done(objs)
        return {"FINISHED"}

    def invoke(self, context, event):
        if _GEN["busy"]:
            self.report({"WARNING"}, "Ya hay una generación en curso")
            return {"CANCELLED"}
        gen = regenerate_iter(context)
        _GEN.update(busy=True, cancel=False, frac=0.0, msg="Preparando")
        # los edificios chicos terminan enseguida y no muestran nada
        t0 = time.time()
        try:
            while time.time() - t0 < 0.35:
                f_, _GEN["msg"] = next(gen)
                _GEN["frac"] = max(_GEN["frac"], f_)
        except StopIteration as e:
            _GEN["busy"] = False
            if e.value is None:
                return {"CANCELLED"}
            self._report_done(e.value)
            return {"FINISHED"}
        except Exception as e:
            _GEN["busy"] = False
            self.report({"ERROR"}, str(e))
            return {"CANCELLED"}
        # sigue: modo con barra de progreso
        self._gen = gen
        wm = context.window_manager
        wm.progress_begin(0, 100)
        self._timer = wm.event_timer_add(0.03, window=context.window)
        wm.modal_handler_add(self)
        self._redraw(context)
        return {"RUNNING_MODAL"}

    def _redraw(self, context):
        pct = int(_GEN["frac"] * 100)
        try:
            context.window_manager.progress_update(pct)
            context.workspace.status_text_set(f"Generando… {pct}% · {_GEN['msg']} · Esc o botón Cancelar para detener")
            for area in context.screen.areas:
                area.tag_redraw()
        except Exception:
            pass

    def _finish(self, context):
        wm = context.window_manager
        if self._timer is not None:
            wm.event_timer_remove(self._timer)
            self._timer = None
        try:
            wm.progress_end()
            context.workspace.status_text_set(None)
            for area in context.screen.areas:
                area.tag_redraw()
        except Exception:
            pass
        _GEN.update(busy=False, cancel=False)

    def modal(self, context, event):
        if event.type == "ESC" and event.value == "PRESS":
            _GEN["cancel"] = True
        if _GEN["cancel"]:
            self._gen.close()   # libera lo que estaba armando; el edificio anterior queda como estaba
            self._finish(context)
            self.report({"WARNING"}, "Generación cancelada: se conservó el edificio anterior")
            return {"CANCELLED"}
        if event.type != "TIMER":
            return {"PASS_THROUGH"}
        t0 = time.time()
        try:
            while time.time() - t0 < 0.04:   # tandas cortas para que la interfaz siga respondiendo
                f_, _GEN["msg"] = next(self._gen)
                _GEN["frac"] = max(_GEN["frac"], f_)
        except StopIteration as e:
            self._finish(context)
            if e.value is None:
                return {"CANCELLED"}
            self._report_done(e.value)
            return {"FINISHED"}
        except Exception as e:
            self._finish(context)
            self.report({"ERROR"}, str(e))
            return {"CANCELLED"}
        self._redraw(context)
        return {"RUNNING_MODAL"}

    def cancel(self, context):
        if self._gen is not None:
            self._gen.close()
        self._finish(context)


class CS2_OT_cancel_generate(bpy.types.Operator):
    bl_idname = "cs2.cancel_generate"
    bl_label = "Cancelar"
    bl_description = "Detiene la generación en curso y conserva el edificio anterior"
    bl_options = {"INTERNAL"}

    def execute(self, context):
        _GEN["cancel"] = True
        return {"FINISHED"}


class CS2_OT_random_seed(bpy.types.Operator):
    bl_idname = "cs2.random_seed"
    bl_label = "Semilla al azar"
    bl_description = "Elige una semilla nueva (otro edificio con los mismos parámetros)"

    def execute(self, context):
        context.scene.cs2_building.seed = random.randint(1, 99999)
        return {"FINISHED"}


class CS2_OT_sample_pieces(bpy.types.Operator):
    bl_idname = "cs2.sample_pieces"
    bl_label = "Crear piezas de ejemplo"
    bl_description = "Crea 2 ventanas y 1 puerta de 2 m x 2 m como modelo para hacer las tuyas"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        s = context.scene.cs2_building
        wcoll, dcoll = create_sample_pieces(context)
        s.window_collection = wcoll
        s.door_collection = dcoll
        self.report({"INFO"}, "Piezas creadas al costado del edificio: editalas o reemplazalas")
        return {"FINISHED"}



# ----------------------------------------------------------------------------
# Edificio hecho a mano: UV, texturas y LODs con el generador
# ----------------------------------------------------------------------------
MANUAL_ROLES = (("glass", ("glass", "vidrio", "ventana", "window", "cristal")),
                ("gar", ("gar", "porton", "portón", "garage", "garaje")),
                ("door", ("door", "puerta")),
                ("rail", ("rail", "baranda", "reja", "herrer", "fence")),
                ("roof", ("roof", "techo", "tejado", "cubierta")),
                ("trim", ("trim", "moldura", "marco", "cornisa", "frame")),
                ("facb", ("facb",)), ("facc", ("facc",)), ("facd", ("facd",)),
                ("facade", ("facade", "fachada", "pared", "wall", "muro")))


def _manual_role(mat):
    """Función de un material del modelo a mano (fachada, vidrio, techo...) por su nombre o, si es transparente, vidrio."""
    import re
    if mat is None:
        return "facade"
    toks = [t for t in re.split(r"[^a-z0-9áéíóúñ]+", mat.name.lower()) if t]
    for role, keys in MANUAL_ROLES:
        for t in toks:
            if any(t.startswith(k) for k in keys):
                return role
    try:
        bs = mat.node_tree.nodes.get("Principled BSDF") if mat.node_tree else None
        if bs is not None:
            tr = bs.inputs.get("Transmission Weight")
            al = bs.inputs.get("Alpha")
            if (tr is not None and tr.default_value > 0.5) or (al is not None and al.default_value < 0.9):
                return "glass"
    except Exception:
        _log_exc("_manual_role")
    return "facade"


def manual_geometry(obj):
    """bmesh del modelo con modificadores y transformación aplicados, índices de material del generador, centrado en
    la base (x, y al centro; z mínimo en 0). Devuelve (bmesh, (cx, cy, z0), (ancho, fondo, alto))."""
    dg = bpy.context.evaluated_depsgraph_get()
    me = bpy.data.meshes.new_from_object(obj.evaluated_get(dg))
    try:
        me.transform(obj.matrix_world)
        roles = [_manual_role(m) for m in me.materials]
        bm = bmesh.new()
        bm.from_mesh(me)
    finally:
        bpy.data.meshes.remove(me)
    for f in list(bm.faces):
        i = f.material_index
        f.material_index = MAT[roles[i] if i < len(roles) else "facade"]
    for lay in list(bm.loops.layers.uv):
        bm.loops.layers.uv.remove(lay)
    if not bm.verts:
        bm.free()
        raise RuntimeError("La malla está vacía")
    xs = [v.co.x for v in bm.verts]
    ys = [v.co.y for v in bm.verts]
    zs = [v.co.z for v in bm.verts]
    cx, cy, z0 = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2, min(zs)
    bmesh.ops.translate(bm, vec=(-cx, -cy, -z0), verts=bm.verts)
    bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=1e-5)
    bm.normal_update()
    return bm, (cx, cy, z0), (max(xs) - min(xs), max(ys) - min(ys), max(zs) - min(zs))


def _manual_finish(geo, p, lod, mats, decimate=1.0, drop=()):
    """Malla (con UV de atlas y materiales del generador) a partir de la geometría `geo`; `decimate` < 1 la reduce."""
    src = bpy.data.meshes.new("_cs2_man_geo")
    if drop:
        g2 = geo.copy()
        bmesh.ops.delete(g2, geom=[f for f in g2.faces if f.material_index in drop], context="FACES")
        g2.to_mesh(src)
        g2.free()
    else:
        geo.to_mesh(src)
    try:
        if decimate < 0.999:
            tmp = bpy.data.objects.new("_cs2_man_tmp", src)
            bpy.context.scene.collection.objects.link(tmp)
            try:
                md = tmp.modifiers.new("dec", "DECIMATE")
                md.decimate_type = "COLLAPSE"
                md.ratio = max(0.001, decimate)
                md.use_collapse_triangulate = False
                bpy.context.view_layer.update()
                ev = tmp.evaluated_get(bpy.context.evaluated_depsgraph_get())
                me2 = bpy.data.meshes.new_from_object(ev)
            finally:
                bpy.data.objects.remove(tmp, do_unlink=True)
        else:
            me2 = src.copy()
    finally:
        bpy.data.meshes.remove(src)
    bm = bmesh.new()
    try:
        bm.from_mesh(me2)
        bpy.data.meshes.remove(me2)
        for lay in list(bm.loops.layers.uv):
            bm.loops.layers.uv.remove(lay)
        bm.loops.layers.uv.new("UVMap")
        bm.faces.layers.int.new("is_piece")
        p["_dissolve"] = lod >= 1
        finish_bmesh(bm, p)
        mesh = bpy.data.meshes.new(f"_cs2_man_{lod}")
        bm.to_mesh(mesh)
    finally:
        bm.free()
    for m_ in mats:
        mesh.materials.append(m_)
    return mesh


def _manual_hull(geo, depth):
    """Casco simple del edificio: remallado en bloques (queda como una caja con los retranqueos grandes), con el
    material de la cara más cercana del modelo; las ventanas, puertas y portones originales se apoyan sobre su superficie."""
    from mathutils.bvhtree import BVHTree
    keep = (MAT["glass"], MAT["door"], MAT["gar"])
    src = bpy.data.meshes.new("_cs2_man_h")
    geo.to_mesh(src)
    tmp = bpy.data.objects.new("_cs2_man_h", src)
    bpy.context.scene.collection.objects.link(tmp)
    try:
        md = tmp.modifiers.new("r", "REMESH")
        md.mode = "BLOCKS"
        md.octree_depth = int(depth)
        md.scale = 0.99
        md.use_remove_disconnected = False
        bpy.context.view_layer.update()
        hm = bpy.data.meshes.new_from_object(tmp.evaluated_get(bpy.context.evaluated_depsgraph_get()))
    finally:
        bpy.data.objects.remove(tmp, do_unlink=True)
        bpy.data.meshes.remove(src)
    hull = bmesh.new()
    hull.from_mesh(hm)
    bpy.data.meshes.remove(hm)
    bmesh.ops.dissolve_limit(hull, angle_limit=math.radians(1.0), verts=hull.verts, edges=hull.edges)
    hull.normal_update()
    body = bmesh.new()
    body.from_mesh(_bm_to_tmp_mesh(geo))
    body.faces.ensure_lookup_table()
    drop = [f for f in body.faces if f.material_index in keep or f.material_index in (MAT["rail"], MAT["trim"])]
    if len(drop) < len(body.faces):
        bmesh.ops.delete(body, geom=drop, context="FACES")
    body.faces.ensure_lookup_table()
    bt = BVHTree.FromBMesh(body) if body.faces else None
    for f in hull.faces:
        f.material_index = MAT["facade"]
        if bt is not None:
            hit = bt.find_nearest(f.calc_center_median())
            if hit and hit[2] is not None and 0 <= hit[2] < len(body.faces):
                f.material_index = body.faces[hit[2]].material_index
    body.free()
    ht = BVHTree.FromBMesh(hull)
    for f in geo.faces:
        if f.material_index not in keep:
            continue
        n = f.normal.copy()
        c = f.calc_center_median()
        loc, _nn, _i, _d = ht.ray_cast(c + n * 12.0, -n, 24.0)
        shift = (loc + n * 0.03 - c) if loc is not None else Vector((0, 0, 0))
        vs = [hull.verts.new(v.co + shift) for v in f.verts]
        try:
            hull.faces.new(vs).material_index = f.material_index
        except ValueError:
            pass
    return hull


def _bm_to_tmp_mesh(bm):
    me = bpy.data.meshes.new("_cs2_man_tm")
    bm.to_mesh(me)
    _TMP_MESHES.append(me)
    return me


_TMP_MESHES = []


def _manual_lod2(geo, p, mats, hi_):
    """LOD2 de un modelo a mano: casco en bloques + ventanas/puertas/portones apoyados. Prueba la resolución más fina
    que entre en el presupuesto."""
    xs = [v.co.x for v in geo.verts]
    ys = [v.co.y for v in geo.verts]
    zs = [v.co.z for v in geo.verts]
    big = max(max(xs) - min(xs), max(ys) - min(ys), max(zs) - min(zs), 1.0)
    d0 = min(7, max(3, int(math.ceil(math.log2(big / 0.6)))))
    try:
        for depth in range(d0, 2, -1):
            hull = _manual_hull(geo, depth)
            try:
                mesh = _manual_finish(hull, p, 2, mats, 1.0)
            finally:
                hull.free()
            if mesh_tris(mesh) <= hi_:
                return mesh
            bpy.data.meshes.remove(mesh)
    finally:
        while _TMP_MESHES:
            try:
                bpy.data.meshes.remove(_TMP_MESHES.pop())
            except (ReferenceError, RuntimeError):
                pass
    return None


def _manual_fit(geo, p, mats, lod, lo, hi_, target):
    """Reduce la geometría hasta que el LOD tenga entre lo y hi_ triángulos (sin contar el vidrio). Si la reducción
    sola no alcanza (piezas sueltas que no se pueden fusionar), primero saca las barandas y después las molduras."""
    def search(drop):
        mesh = _manual_finish(geo, p, lod, mats, 1.0, drop)
        t = mesh_tris(mesh)
        if t <= hi_:
            return mesh
        bpy.data.meshes.remove(mesh)
        ra, ta = 1.0, t              # ra: se pasa
        rb, tb = 0.0, 0.0            # rb: entra
        best = None
        r = min(0.95, max(0.02, target / max(t, 1)))
        for _ in range(10):
            mesh = _manual_finish(geo, p, lod, mats, r, drop)
            t = mesh_tris(mesh)
            if lo <= t <= hi_:
                if best is not None:
                    bpy.data.meshes.remove(best[1])
                return mesh
            if t > hi_:
                ra, ta = r, t
                bpy.data.meshes.remove(mesh)
            else:
                rb, tb = r, t
                if best is not None:
                    bpy.data.meshes.remove(best[1])
                best = (t, mesh)
            if tb > 0:
                r = rb + (target - tb) * (ra - rb) / max(1, ta - tb)
            else:
                r = r * target / max(t, 1)
            r = min(max(r, 0.005), 0.995)
        return best[1] if best is not None else None
    for drop in ((), (MAT["rail"],), (MAT["rail"], MAT["trim"])):
        mesh = search(drop)
        if mesh is not None:
            return mesh
    return _manual_finish(geo, p, lod, mats, 0.01, (MAT["rail"], MAT["trim"]))


def manual_build(context, s, src, only=None):
    """Arma LOD0, LOD1 y LOD2 de un modelo hecho a mano (con UV de atlas y los materiales del generador).
    only=(1, 2): rehace solo esos LODs a partir del LOD0 que ya existe."""
    name = s.building_name or "Residencial01"
    p = props_to_params(s)
    p["building_name"] = name
    mats = get_materials(p)
    coll = get_collection(context, f"CS2_{name}")
    if only is None:
        geo, (cx, cy, z0), (w, d, h) = manual_geometry(src)
        p.pop("_atlas", None)
        p.pop("_ext", None)
        # atlas: lo reparte el LOD0 según el tamaño de sus paredes; LOD1 y LOD2 lo heredan
        mesh0 = _manual_finish(geo, p, 0, mats, 1.0)
        info = {"cx": cx, "cy": cy, "z0": z0, "w": w, "d": d, "h": h}
    else:
        ob0 = bpy.data.objects.get(f"{name}_LOD0")
        if ob0 is None or not ob0.get("cs2_manual"):
            raise RuntimeError("Este edificio no viene de tu malla")
        p["_atlas"], p["_ext"] = bake_context(p)
        bm0 = bmesh.new()
        bm0.from_mesh(ob0.data)
        for lay in list(bm0.loops.layers.uv):
            bm0.loops.layers.uv.remove(lay)
        geo = bm0
        mesh0 = None
        info = json.loads(ob0["cs2_manual"])
    meshes = {}
    try:
        t0 = mesh_tris(mesh0) if mesh0 is not None else mesh_tris(bpy.data.objects[f"{name}_LOD0"])
        if 1 in (only or (1, 2)):
            meshes[1] = _manual_fit(geo, p, mats, 1, 0.46 * t0, 0.497 * t0, 0.48 * t0)
        if 2 in (only or (1, 2)):
            meshes[2] = _manual_lod2(geo, p, mats, LOD2_MAX_TRIS - 3) or _manual_fit(geo, p, mats, 2, 455, LOD2_MAX_TRIS - 3, 482)
        if mesh0 is not None:
            meshes[0] = mesh0
    finally:
        geo.free()
    gap = max(info["w"], 4.0) * 1.4
    objs = []
    for lod in range(3):
        nm = f"{name}_LOD{lod}"
        if lod in meshes:
            old = bpy.data.objects.get(nm)
            loc = None
            if old is not None:
                loc = old.location.copy()
                om = old.data
                bpy.data.objects.remove(old, do_unlink=True)
                if om.users == 0:
                    bpy.data.meshes.remove(om)
            meshes[lod].name = nm
            ob = bpy.data.objects.new(nm, meshes[lod])
            coll.objects.link(ob)
            ob.location = loc if loc is not None else (info["cx"] + (lod + 1) * gap, info["cy"], info["z0"])
        else:
            ob = bpy.data.objects[nm]
        objs.append(ob)
    for ob in objs:
        ob["cs2_ext"] = float(p.get("_ext", ATLAS_SCALE))
        ob["cs2_atlas"] = json.dumps({k: list(v) for k, v in (p.get("_atlas") or ATLAS).items()})
    objs[0]["cs2_manual"] = json.dumps(info)
    try:
        update_preview(dict(p, pbr_preview=False))
    except Exception as e:
        print("CS2: vista previa de texturas:", e)
    return objs


class CS2_OT_from_mesh(bpy.types.Operator):
    bl_idname = "cs2.from_mesh"
    bl_label = "Generar desde mi malla"
    bl_description = ("Usa el objeto activo (un edificio hecho a mano) como LOD0: le arma las UV del atlas con los materiales del "
                      "generador y crea el LOD1 (cerca de la mitad de triángulos) y el LOD2 (cerca de 500). Los materiales se "
                      "reconocen por el nombre: facade, glass, trim, roof, door, gar, rail (o fachada, vidrio, moldura, techo, "
                      "puerta, portón, baranda). Después seguís con UV, sombras, texturas y exportar")
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        ob = context.active_object
        if ob is None or ob.type != "MESH":
            cls.poll_message_set("Seleccioná tu edificio (una malla)")
            return False
        if "_LOD" in ob.name and ob.get("cs2_ext") is not None:
            cls.poll_message_set("Ese objeto ya es un LOD del generador: seleccioná tu malla original")
            return False
        return True

    def execute(self, context):
        s = context.scene.cs2_building
        src = context.active_object
        if not s.building_name:
            s.building_name = "".join(c for c in src.name if c.isalnum() or c in "_-") or "Residencial01"
        try:
            objs = manual_build(context, s, src)
        except RuntimeError as e:
            self.report({"ERROR"}, str(e))
            return {"CANCELLED"}
        tris = [mesh_tris(o) for o in objs]
        roles = sorted({_manual_role(m) for m in src.data.materials}) or ["facade"]
        self.report({"INFO"}, f"LOD0 {tris[0]} · LOD1 {tris[1]} · LOD2 {tris[2]} triángulos. Materiales reconocidos: {', '.join(roles)}")
        return {"FINISHED"}


class CS2_OT_regen_lods(bpy.types.Operator):
    bl_idname = "cs2.regen_lods"
    bl_label = "Generar LODs"
    bl_description = ("Arma el LOD1 y el LOD2 a partir de la malla principal, ajustándolos al presupuesto del juego (LOD1 cerca "
                      "de la mitad de triángulos del LOD0, LOD2 cerca de 500) sin tocar el LOD0. Si ya existían, los rehace: "
                      "sus UV y texturas hay que volver a generarlas")
    bl_options = {"REGISTER", "UNDO"}

    @_with_work("Regenerando LODs")
    def execute(self, context):
        s = context.scene.cs2_building
        if _main_obj(s) is None:
            self.report({"ERROR"}, "Primero creá el edificio")
            return {"CANCELLED"}
        try:
            ob0 = bpy.data.objects.get(f"{s.building_name or 'Residencial01'}_LOD0")
            if ob0 is not None and ob0.get("cs2_manual"):
                objs = manual_build(context, s, None, only=(1, 2))
            else:
                objs = regenerate(context, only=(1, 2))
        except RuntimeError as e:
            self.report({"ERROR"}, str(e))
            return {"CANCELLED"}
        tris = [mesh_tris(o) for o in objs]
        r = tris[1] / tris[0] if tris[0] else 0.0
        self.report({"INFO"}, f"LOD1: {tris[1]} ({r * 100:.0f}% del alto) · LOD2: {tris[2]} triángulos")
        return {"FINISHED"}


class CS2_OT_export(bpy.types.Operator):
    bl_idname = "cs2.export"
    bl_label = "Exportar FBX"
    bl_description = "Exporta un FBX por LOD y uno con todos juntos"

    @_with_work("Exportando para CS2")
    def execute(self, context):
        s = context.scene.cs2_building
        out = export_dir_for(s, self)
        if out is None:
            return {"CANCELLED"}
        objs = _ensure_lods(context, s)     # se exporta lo que ya está creado (con sus UV y sombras); faltan LODs: se generan; no hay edificio: se crea
        if objs is None:
            if safe_regenerate(self, context) is None:
                return {"CANCELLED"}
            objs = _ensure_lods(context, s)
        if objs is None:
            return {"CANCELLED"}
        _o0 = bpy.data.objects.get(f"{s.building_name or 'Residencial01'}_LOD0")
        if _o0 is not None and _o0.get("cs2_manual"):
            _mi = json.loads(_o0["cs2_manual"])
            w, d = _mi["w"], _mi["d"]
        else:
            w, d = (load_shape_for(s)["size"].xy if s.mode == "shape" else footprint(s))
        before = tex_status(s)
        asset = export_building(context, objs, s.building_name or "Residencial01", out,
                                s.theme, level_for(s), (w, d), props_to_params(s), clean=s.clean_on_export)
        if not before["uv"] or (before["mode"] == "BAKE" and not before["bake"]):
            self.report({"INFO"}, "Completé solo los pasos de textura que faltaban (UV y sombras)")
        warn = lot_warning(w, d)
        if warn:
            self.report({"WARNING"}, warn)
        for text, ok in lod_report(objs):
            if not ok:
                self.report({"WARNING"}, "Presupuesto de triángulos: " + text)
        self.report({"INFO"}, f"Exportado: ProjectFiles/{asset}/ en {out}")
        return {"FINISHED"}


class CS2_OT_cs1_generate(bpy.types.Operator):
    bl_idname = "cs2.cs1_generate"
    bl_label = "Generar para CS1"
    bl_description = ("Arma la malla compacta para Cities: Skylines 1 (con su LOD) al lado de los LODs de CS2. Las ventanas, "
                      "puertas, portones y marcos van como imagen con relieve (normal), no como geometría")
    bl_options = {"REGISTER", "UNDO"}

    @_with_work("Generando para CS1")
    def execute(self, context):
        s = context.scene.cs2_building
        try:
            info = cs1_generate(context, s)
        except RuntimeError as e:
            self.report({"ERROR"}, str(e))
            return {"CANCELLED"}
        msg = f"CS1: malla {info['tris']} triángulos · LOD {info['lod_tris']}"
        if info["tris"] > int(s.cs1_tris):
            msg += f" (el presupuesto de {int(s.cs1_tris)} no alcanza ni con lo mínimo)"
        if info["lost"]:
            msg += f" · {info['lost']} imágenes de aberturas no entraron en la textura"
        self.report({"WARNING" if (info["lost"] or info["tris"] > int(s.cs1_tris)) else "INFO"}, msg)
        return {"FINISHED"}


class CS2_OT_export_cs1(bpy.types.Operator):
    bl_idname = "cs2.export_cs1"
    bl_label = "Exportar para CS1"
    bl_description = "Exporta el FBX principal, el _lod y los mapas _d/_n/_s/_i de la malla CS1 (la genera si falta)"

    @_with_work("Exportando para CS1")
    def execute(self, context):
        s = context.scene.cs2_building
        out = export_dir_for(s, self)
        if out is None:
            return {"CANCELLED"}
        try:
            if cs1_objs(s) is None:
                cs1_generate(context, s)
            name = s.building_name or "Residencial01"
            p = dict(props_to_params(s), building_name=name)
            info = export_building_cs1(context, s, out, p, snow=s.cs1_snow, scale=s.cs1_scale)
        except Exception as e:
            log_error(getattr(self, "bl_idname", "operador"), e)
            self.report({"ERROR"}, f"No se pudo exportar para CS1: {e}")
            return {"CANCELLED"}
        if info["verts"] > CS1_MAX_VERTS:
            self.report({"WARNING"}, f"La malla tiene {info['verts']} vértices (máx. {CS1_MAX_VERTS} en CS1)")
        w, d = (load_shape_for(s)["size"].xy if s.mode == "shape" else footprint(s))
        warn = cs1_lot_text(w, d, s.asset_category == "ZONED")
        if warn:
            self.report({"WARNING"}, warn)
        self.report({"INFO"}, f"CS1: {info['asset']} · {info['tris']} tri + lod {info['lod_tris']} tri · {info['folder']}")
        return {"FINISHED"}


def find_leaks(mesh, shape_obj, probes=300, rays=40, seed=1):
    """Revisa si la malla generada deja huecos: dispara rayos desde puntos interiores y cuenta los que escapan.
    Devuelve (puntos_probados, fugas). Ignora puntos pegados a la pared (dentro del hueco de una ventana)."""
    import random
    from mathutils.bvhtree import BVHTree
    me = mesh.copy()
    try:
        me.calc_loop_triangles()
        verts = [v.co.copy() for v in me.vertices]
        tris = [tuple(t.vertices) for t in me.loop_triangles]
        if not tris:
            return 0, 0
        bvh = BVHTree.FromPolygons(verts, tris, epsilon=1e-5)
        sbm = bmesh.new()
        sbm.from_mesh(shape_obj.data)
        sbm.transform(shape_obj.matrix_world)
        sb = BVHTree.FromBMesh(sbm)
        cx = (min(v.co.x for v in sbm.verts) + max(v.co.x for v in sbm.verts)) / 2
        cy = (min(v.co.y for v in sbm.verts) + max(v.co.y for v in sbm.verts)) / 2
        zmin = min(v.co.z for v in sbm.verts)
        sbm.free()
        bb = [(min(c), max(c)) for c in zip(*[tuple(v) for v in verts])]
        rnd = random.Random(seed)
        dirs = []
        for _ in range(rays):
            z = rnd.uniform(0.0, 1.0)
            ph = rnd.uniform(0, 2 * math.pi)
            r = math.sqrt(1 - z * z)
            dirs.append(Vector((r * math.cos(ph), r * math.sin(ph), z)))
        n_in = n_bad = 0
        for _ in range(probes):
            p = Vector((rnd.uniform(*bb[0]), rnd.uniform(*bb[1]), rnd.uniform(1.0, max(1.1, bb[2][1] - 1.0))))
            q = Vector((p.x + cx, p.y + cy, p.z + zmin))
            hits, o_ = 0, q.copy()
            d0 = Vector((1, 0.0013, 0.0007))
            while True:
                h = sb.ray_cast(o_, d0, 1e3)
                if h[0] is None:
                    break
                hits += 1
                o_ = h[0] + d0 * 1e-4
            if hits % 2 == 0:
                continue
            nn = sb.find_nearest(q)
            if nn[0] is not None and nn[3] < 0.6:
                continue
            n_in += 1
            if any(bvh.ray_cast(p, d, 1e4)[0] is None for d in dirs):
                n_bad += 1
        return n_in, n_bad
    finally:
        bpy.data.meshes.remove(me)


class CS2_OT_check_holes(bpy.types.Operator):
    bl_idname = "cs2.check_holes"
    bl_label = "Revisar huecos en la malla"
    bl_description = ("Dispara rayos desde adentro del edificio para ver si quedó algún hueco. Si encuentra, "
                      "regenera sin quitar caras ocultas (esa limpieza es lo único que puede borrar paredes visibles)")
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        s = context.scene.cs2_building
        name = s.building_name or "Residencial01"
        ob = bpy.data.objects.get(f"{name}_LOD0")
        if ob is None or s.shape_object is None:
            self.report({"ERROR"}, "Primero generá el edificio desde una forma")
            return {"CANCELLED"}
        n_in, bad = find_leaks(ob.data, s.shape_object)
        if bad == 0:
            self.report({"INFO"}, f"Sin huecos ({n_in} puntos revisados)")
            return {"FINISHED"}
        if s.clean_on_generate or s.clean_on_export:
            s.clean_on_generate = False
            s.clean_on_export = False
            safe_regenerate(self, context)
            ob = bpy.data.objects.get(f"{name}_LOD0")
            n2, bad2 = find_leaks(ob.data, s.shape_object) if ob else (0, 0)
            self.report({"WARNING"}, f"Había {bad} huecos; regenerado sin quitar caras ocultas: ahora {bad2}. "
                                     "Si siguen, mandame una captura del lugar")
        else:
            self.report({"WARNING"}, f"Hay {bad} huecos aunque no se quitan caras ocultas: revisá la forma base")
        return {"FINISHED"}


class CS2_OT_clean_mesh(bpy.types.Operator):
    bl_idname = "cs2.clean_mesh"
    bl_label = "Limpiar malla (quitar caras ocultas)"
    bl_description = ("Elimina de los LOD las caras que no se ven desde afuera. Al volver a generar el edificio "
                      "se vuelven a crear: la exportación ya puede limpiarlas sola")
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        s = context.scene.cs2_building
        name = s.building_name or "Residencial01"
        objs = [bpy.data.objects.get(f"{name}_LOD{lod}") for lod in range(3)]
        objs = [o for o in objs if o is not None]
        if not objs:
            self.report({"ERROR"}, "Primero generá el edificio")
            return {"CANCELLED"}
        txt = []
        for o in objs:
            a, t = clean_hidden_faces(o.data)
            txt.append(f"{o.name.rsplit('_', 1)[-1]}: -{a} de {t}")
        self.report({"INFO"}, "Caras ocultas quitadas · " + " · ".join(txt))
        return {"FINISHED"}


def remove_grid():
    old = bpy.data.objects.get("CS2_Grid")
    if old is not None:
        me = old.data
        bpy.data.objects.remove(old, do_unlink=True)
        if me is not None and me.users == 0:
            bpy.data.meshes.remove(me)


def _lot_wd(s):
    """(ancho, fondo) del lote: de la forma si ya hay una, y si no de las celdas o las medidas. No crea nada."""
    if s.shape_object is not None and s.shape_object.type == "MESH":
        try:
            sz = load_shape_for(s)["size"]
            return sz.x, sz.y
        except Exception:
            _log_exc("_lot_wd")
    return footprint(s)


def make_grid(context):
    """Dibuja la cuadrícula de celdas de 8 m de Cities: Skylines 2 alrededor del lote. Devuelve (cx, cz, nx, nz)."""
    s = context.scene.cs2_building
    w, d = _lot_wd(s)
    cx, cz = lot_cells(w, d)
    nx, nz = cx + 2, cz + 2  # un margen de una celda por lado
    x0, y0 = -nx * CELL / 2, -nz * CELL / 2
    verts, edges = [], []
    for i in range(nx + 1):
        verts += [(x0 + i * CELL, y0, 0.02), (x0 + i * CELL, y0 + nz * CELL, 0.02)]
        edges.append((len(verts) - 2, len(verts) - 1))
    for j in range(nz + 1):
        verts += [(x0, y0 + j * CELL, 0.02), (x0 + nx * CELL, y0 + j * CELL, 0.02)]
        edges.append((len(verts) - 2, len(verts) - 1))
    remove_grid()
    mesh = bpy.data.meshes.new("CS2_Grid")
    mesh.from_pydata(verts, edges, [])
    obj = bpy.data.objects.new("CS2_Grid", mesh)
    obj.display_type = "WIRE"
    obj.hide_render = True
    obj.hide_select = True
    context.scene.collection.objects.link(obj)
    return cx, cz, nx, nz


class CS2_OT_grid(bpy.types.Operator):
    bl_idname = "cs2.grid"
    bl_label = "Cuadrícula de 8 m"
    bl_description = "Dibuja la cuadrícula de celdas de Cities: Skylines 2 con tu lote al centro"

    def execute(self, context):
        try:
            cx, cz, nx, nz = make_grid(context)
        except RuntimeError as e:
            self.report({"ERROR"}, str(e))
            return {"CANCELLED"}
        self.report({"INFO"}, f"Grilla de {nx}x{nz} celdas (lote {cx}x{cz} al centro)")
        return {"FINISHED"}


class CS2_OT_delete_all(bpy.types.Operator):
    bl_idname = "cs2.delete_all"
    bl_label = "Borrar todo"
    bl_description = ("Borra el edificio creado (LODs de CS2 y malla de CS1), la forma base y la cuadrícula. Los valores y estilos "
                      "que elegiste se conservan; para volver también a los valores de fábrica usá «Restablecer»")
    bl_options = {"REGISTER", "UNDO"}

    def invoke(self, context, event):
        return context.window_manager.invoke_confirm(self, event, message="Se borra el edificio, la malla de CS1 y la forma. ¿Seguir?")

    def execute(self, context):
        s = context.scene.cs2_building
        try:
            if context.object is not None and context.object.mode == "EDIT":
                bpy.ops.object.mode_set(mode="OBJECT")
        except Exception:
            _log_exc("execute")
        set_building_lock(context, False)
        live = s.live_update
        s.live_update = False
        n_del = 0
        try:
            name = s.building_name or "Residencial01"
            coll = bpy.data.collections.get(f"CS2_{name}")
            objs = list(coll.objects) if coll else []
            for nm in [f"{name}_LOD{i}" for i in range(3)] + list(cs1_names(name)):
                o = bpy.data.objects.get(nm)
                if o is not None and o not in objs:
                    objs.append(o)
            shape = s.shape_object
            if shape is not None and shape not in objs:
                objs.append(shape)
            s.shape_object = None
            for o in objs:
                me = o.data
                bpy.data.objects.remove(o, do_unlink=True)
                n_del += 1
                if me is not None and getattr(me, "users", 1) == 0:
                    try:
                        bpy.data.meshes.remove(me)
                    except Exception:
                        _log_exc("execute")
            for sn in (f"{cs1_names(name)[0]}_atlas",):
                m_ = bpy.data.meshes.get(sn)
                if m_ is not None:
                    bpy.data.meshes.remove(m_)
            if coll is not None:
                bpy.data.collections.remove(coll)
            remove_grid()
            _ROOF_SNAP[0] = None
        finally:
            s.live_update = live
        _LAST_ACTIVE["key"] = None
        self.report({"INFO"}, f"Borrado: {n_del} objeto(s). Elegí una forma para empezar de nuevo")
        return {"FINISHED"}


class CS2_OT_reset(bpy.types.Operator):
    bl_idname = "cs2.reset"
    bl_label = "Restablecer todo"
    bl_description = "Borra el edificio generado y la forma, y vuelve todos los valores a los de fábrica para empezar de nuevo"
    bl_options = {"REGISTER", "UNDO"}

    def invoke(self, context, event):
        return context.window_manager.invoke_confirm(self, event, message="Se borra el edificio y la forma, y todo vuelve a los valores de fábrica. ¿Seguir?")

    def execute(self, context):
        s = context.scene.cs2_building
        try:
            if bpy.context.object is not None and bpy.context.object.mode == "EDIT":
                bpy.ops.object.mode_set(mode="OBJECT")
        except Exception:
            _log_exc("execute")
        set_building_lock(context, False)
        live = s.live_update
        s.live_update = False
        try:
            name = s.building_name or "Residencial01"
            coll = bpy.data.collections.get(f"CS2_{name}")
            objs = list(coll.objects) if coll else []
            for lod in range(3):
                o = bpy.data.objects.get(f"{name}_LOD{lod}")
                if o is not None and o not in objs:
                    objs.append(o)
            shape = s.shape_object
            if shape is not None and shape not in objs:
                objs.append(shape)
            s.shape_object = None
            for o in objs:
                me = o.data
                bpy.data.objects.remove(o, do_unlink=True)
                if me is not None and getattr(me, "users", 1) == 0:
                    try:
                        bpy.data.meshes.remove(me)
                    except Exception:
                        _log_exc("execute")
            if coll is not None:
                bpy.data.collections.remove(coll)
            remove_grid()
            _ROOF_SNAP[0] = None
            _FICHA_STATE["dirty"] = False
            reset_building_props(s, everything=True)
            for grp in (context.scene.cs2_wall,):
                for prop in grp.bl_rna.properties:
                    if prop.identifier != "rna_type":
                        try:
                            grp.property_unset(prop.identifier)
                        except Exception:
                            _log_exc("execute")
        finally:
            s.live_update = live
        _LAST_ACTIVE["key"] = None
        self.report({"INFO"}, "Todo restablecido: elegí el lote y la planta para empezar")
        return {"FINISHED"}


class CS2_OT_batch(bpy.types.Operator):
    bl_idname = "cs2.batch"
    bl_label = "Generar lote"
    bl_description = "Genera varios edificios distintos (semilla, semilla+1, ...) en una grilla"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        s = context.scene.cs2_building
        out = None
        if s.batch_export:
            out = export_dir_for(s, self)
            if out is None:
                return {"CANCELLED"}
        base = s.building_name or "Residencial01"
        coll = get_collection(context, f"CS2_Lote_{base}")
        for o in list(coll.objects):  # reemplaza el lote anterior
            me = o.data
            bpy.data.objects.remove(o, do_unlink=True)
            if me is not None and me.users == 0:
                bpy.data.meshes.remove(me)

        shape = None
        if s.mode == "shape":
            try:
                shape = load_shape_for(s)
            except RuntimeError as e:
                self.report({"ERROR"}, str(e))
                return {"CANCELLED"}
            w, d = shape["size"].x, shape["size"].y
        else:
            w, d = footprint(s)
        n = s.batch_count
        cols = max(1, math.ceil(math.sqrt(n)))
        step_x = w + s.batch_spacing
        step_y = d + s.batch_spacing + 1.5
        y0 = d + 10.0  # detrás del edificio principal
        for i in range(n):
            p = props_to_params(s, seed=s.seed + i)
            p["building_name"] = f"{base}_{i + 1:02d}"
            if shape:
                p["width"], p["depth"] = w, d
            origin = ((i % cols) * step_x, y0 + (i // cols) * step_y, 0.0)
            objs = make_building(context, p, coll, origin, separate=False, hide_lods=True, shape=shape)
            if out:
                export_building(context, objs, p["building_name"], out, s.theme, level_for(s), (w, d), p)

        flat = s.variation == 0 and s.floors_jitter == 0 and s.balconies != "random"
        if flat:
            self.report({"WARNING"}, "Salieron todos iguales: subí Variación, Variar pisos o usá balcones al azar")
        else:
            self.report({"INFO"}, f"Lote de {n} edificios creado" + (f" y exportado en {out}" if out else ""))
        return {"FINISHED"}


def shape_ghost(obj):
    """La forma base se ve como un volumen transparente (con sus aristas) hasta que se crea el edificio."""
    try:
        m = bpy.data.materials.get("CS2_Forma_fantasma")
        if m is None:
            m = bpy.data.materials.new("CS2_Forma_fantasma")
            m.use_nodes = True
            bs = m.node_tree.nodes.get("Principled BSDF")
            if bs is not None:
                bs.inputs["Base Color"].default_value = (0.25, 0.55, 1.0, 1.0)
                bs.inputs["Alpha"].default_value = 0.25
            try:
                m.surface_render_method = "BLENDED"
            except Exception:
                _log_exc("shape_ghost")
        m.diffuse_color = (0.25, 0.55, 1.0, 0.25)
        if obj.data.materials:
            obj.data.materials[0] = m
        else:
            obj.data.materials.append(m)
        obj.display_type = "SOLID"
        obj.show_transparent = True
        obj.show_wire = True
        obj.show_all_edges = True
        obj.hide_render = True
        obj["cs2_wire_done"] = False
    except Exception as e:
        print("CS2: forma transparente:", e)


class CS2_OT_create_shape(bpy.types.Operator):
    bl_idname = "cs2.create_shape"
    bl_label = "Crear forma base"
    bl_description = "Crea una malla base (caja, L, T o U) para editar con Tab y extruir con E"
    bl_options = {"REGISTER", "UNDO"}

    kind: EnumProperty(name="Planta", default="BOX",
                       items=[("BOX", "Caja", ""), ("L", "L", ""), ("T", "T", ""), ("U", "U", "")])
    keep: bpy.props.BoolProperty(name="Conservar valores", default=False, options={"SKIP_SAVE"})

    def execute(self, context):
        s = context.scene.cs2_building
        if not self.keep and _FICHA_STATE.get("dirty"):   # venía de una ficha: vuelve a los valores de fábrica
            reset_building_props(s)
        _FICHA_STATE["dirty"] = False
        w, d = footprint(s)
        obj = create_shape_object(context, self.kind, w, d, s.floors * s.floor_height, s.floor_height)
        for o in context.view_layer.objects:
            o.select_set(False)
        obj.select_set(True)
        context.view_layer.objects.active = obj
        live = s.live_update
        s.live_update = False  # un solo regenerado al final
        s.shape_object = obj
        s.mode = "shape"
        s.live_update = live
        shape_ghost(obj)
        self.report({"INFO"}, "Forma creada (transparente): Tab para editarla, E para extruir caras. Cuando esté lista, «Crear el edificio»")
        return {"FINISHED"}


def _count_pieces(coll):
    return 0 if coll is None else sum(1 for o in coll.all_objects if o.type == "MESH")


def _has_shape(s):
    return s.shape_object is not None and s.shape_object.type == "MESH"


def _lod_objs(s):
    """Los tres LODs, o None si falta alguno."""
    name = s.building_name or "Residencial01"
    objs = [bpy.data.objects.get(f"{name}_LOD{lod}") for lod in range(3)]
    return objs if all(o is not None for o in objs) else None


def _main_obj(s):
    """La malla principal (LOD0) del edificio, o None si todavía no se creó."""
    return bpy.data.objects.get(f"{s.building_name or 'Residencial01'}_LOD0")


def _existing_lods(s):
    """Los LODs que ya existen (siempre empezando por el LOD0), en orden."""
    name = s.building_name or "Residencial01"
    out = []
    for lod in range(3):
        o = bpy.data.objects.get(f"{name}_LOD{lod}")
        if o is None:
            break
        out.append(o)
    return out


def _ensure_lods(context, s):
    """Los tres LODs; si faltan el LOD1 y el LOD2 los genera a partir de la malla principal. None si no hay malla principal."""
    objs = _lod_objs(s)
    if objs is not None:
        return objs
    ob0 = _main_obj(s)
    if ob0 is None:
        return None
    if ob0.get("cs2_manual"):
        return manual_build(context, s, None, only=(1, 2))
    return regenerate(context, only=(1, 2))


def _shape_size(s):
    """(ancho, fondo, alto) de la forma elegida, o None."""
    try:
        sz = load_shape_for(s)["size"]
        return sz.x, sz.y, sz.z
    except Exception:
        _log_exc("tamano_forma")
        return None


def _checklist(s):
    """Lista de control antes de exportar: [(ok, texto)]."""
    out = []
    has = _has_shape(s)
    out.append((has, "Elegiste una forma" if has else "Falta elegir la forma (paso 1)"))
    main = _main_obj(s)
    objs = _lod_objs(s)
    out.append((main is not None, "El edificio está creado" if main else "Falta crear el edificio"))
    if main is not None and objs is None:
        out.append((False, "Faltan el LOD1 y el LOD2: se generan solos al exportar (o con «Generar LODs»)"))
    if objs:
        bad = [t for t, ok in lod_report(objs) if not ok]
        out.append((not bad, "Los tres niveles de detalle cumplen los límites del juego" if not bad else bad[0] + " · se pasa del límite"))
    if main is not None:
        st = tex_status(s)
        out.append((st["uv"], "UV generadas" if st["uv"] else "Faltan las UV (pestaña 3): se generan solas al exportar"))
        if st["mode"] == "BAKE":
            out.append((st["bake"], "Sombras horneadas" if st["bake"] else "Faltan hornear las sombras (pestaña 3): se hornean solas al exportar"))
    probs = texture_problems(s)
    if probs:
        out.append((False, "Texturas: " + texture_problem_text(probs)))
    elif any(getattr(s, f"ms_{sl}_mode") == "PBR" for sl in PBR_SLOTS):
        out.append((True, "Archivos de textura encontrados"))
    if has:
        sz = _shape_size(s)
        if sz:
            warn = lot_warning(sz[0], sz[1])
            out.append((warn is None, "El lote entra en el juego" if warn is None else warn))
    if main is not None:
        ov = lot_overhang(main)
        if ov is not None:
            out.append((ov <= 0.5, "La malla queda dentro del lote" if ov <= 0.5 else
                        f"La malla se sale {ov:.1f} m del lote: conviene dejar un retiro y pocos voladizos"))
        if any(getattr(s, f"ms_{sl}_mode") == "PBR" for sl in PBR_SLOTS) or s.bake_ao:
            big = int(s.tex_size) >= 2048
            out.append((big, "Textura de %s px" % s.tex_size if big else
                        "Textura de %s px: el juego usa 4096 de referencia, con 1024 se ve borroso" % s.tex_size))
    out.append((bool(s.export_dir), "Carpeta de exportación elegida" if s.export_dir else "Falta elegir la carpeta de exportación"))
    return out


def lot_overhang(ob):
    """Metros que la malla principal se sale del lote (celdas de 8 m, centrado en el origen); None si no se puede medir."""
    try:
        import numpy as np
        me = ob.data
        n = len(me.vertices)
        co = np.empty(n * 3, np.float32)
        me.vertices.foreach_get("co", co)
        co = co.reshape(n, 3)
        w, d = float(co[:, 0].max() - co[:, 0].min()), float(co[:, 1].max() - co[:, 1].min())
        cx, cz = lot_cells(w, d)
        # el lote es el rectángulo de celdas centrado en la planta: sale lo que sobra del ancho/fondo de las celdas
        over = max(float(co[:, 0].max() - cx * CELL / 2), float(-co[:, 0].min() - cx * CELL / 2),
                   float(co[:, 1].max() - cz * CELL / 2), float(-co[:, 1].min() - cz * CELL / 2), 0.0)
        return over
    except Exception:
        _log_exc("exceso_lote")
        return None


def _checklist_cs1(s):
    """Lista de control para CS1: [(ok, texto)]."""
    out = []
    has = _has_shape(s)
    out.append((has, "Elegiste una forma" if has else "Falta elegir la forma (paso 1)"))
    out.append((_main_obj(s) is not None, "El edificio de CS2 está creado (de ahí salen las texturas)" if _main_obj(s) else "Falta crear el edificio"))
    c1 = cs1_objs(s)
    out.append((c1 is not None, "Malla CS1 generada" if c1 else "Falta generar la malla CS1 (se genera sola al exportar)"))
    if c1:
        nv = len(c1[0].data.loops)
        out.append((nv <= CS1_MAX_VERTS, f"Vértices: {nv} de {CS1_MAX_VERTS} permitidos" if nv <= CS1_MAX_VERTS
                    else f"Vértices: {nv}, el límite de CS1 es {CS1_MAX_VERTS}"))
    if has:
        sz = _shape_size(s)
        if sz:
            warn = cs1_lot_text(sz[0], sz[1], s.asset_category == "ZONED")
            out.append((warn is None, "El lote entra en CS1" if warn is None else warn))
    nm = s.building_name or "Residencial01"
    if cs1_name(nm) != nm:
        out.append((True, f"El nombre se exporta como «{cs1_name(nm)}» (sin espacios ni guiones bajos)"))
    out.append((bool(s.export_dir), "Carpeta de exportación elegida" if s.export_dir else "Falta elegir la carpeta de exportación"))
    return out


def _draw_resumen(lay, s):
    box = lay.box()
    if not _has_shape(s):
        box.label(text="Todavía no hay edificio: empezá por el paso 1 · Lote y forma.", icon="INFO")
        return
    zt = ZONE_TYPES.get(s.zone_type, ("", ""))[0] if s.zone_type != "OTHER" else ""
    box.label(text=ASSET_CATEGORY_TEXT.get(s.asset_category, "") + (f" · {zt}" if zt else ""), icon="PREFERENCES")
    sz = _shape_size(s)
    if sz:
        w, d, h = sz
        cx, cz = lot_cells(w, d)
        floors = max(1, int(round(h / max(0.5, s.floor_height))))
        box.label(text=f"{floors} pisos · {w:.0f} × {d:.0f} m · lote de {cx} × {cz} celdas", icon="HOME")
    objs = _lod_objs(s)
    if objs:
        ok = all(o for _t, o in lod_report(objs))
        tris = [mesh_tris(o) for o in objs]
        row = box.row()
        row.alert = not ok
        row.label(text=f"Triángulos: alto {tris[0]} · medio {tris[1]} · bajo {tris[2]}",
                  icon="CHECKMARK" if ok else "ERROR")
    elif _main_obj(s) is not None:
        box.label(text=f"Triángulos: {mesh_tris(_main_obj(s))} · LODs sin generar", icon="INFO")
    todo = [t for ok, t in _checklist(s) if not ok]
    if todo:
        box.label(text=todo[0], icon="DOT")
    else:
        box.label(text="Listo para exportar", icon="CHECKMARK")


UI_TABS = (("FORMA", "1 · Lote y forma", "Tipo de asset, lote, forma y crear el edificio", "MESH_DATA"),
           ("PERS", "2 · Personalizar", "Fachada, techo y extras", "MOD_BUILD"),
           ("TEX", "3 · Texturas", "Materiales, mapa UV, horneado y asignación", "TEXTURE"),
           ("EXPORT", "4 · Exportar", "Lista de control y exportación", "EXPORT"))


def _tab_poll(tab):
    def poll(cls, context):
        s = getattr(context.scene, "cs2_building", None)
        return s is not None and s.ui_tab == tab
    return classmethod(poll)


def _draw_build_button(lay, s, big=1.6):
    """Botón de crear/actualizar el edificio (con barra de progreso mientras trabaja). Va al final de «Forma» y en la barra de la vista 3D."""
    has = _main_obj(s) is not None
    if _GEN["busy"]:
        row = lay.row(align=True)
        if hasattr(lay, "progress"):
            row.progress(factor=_GEN["frac"], type="BAR", text=f"{int(_GEN['frac'] * 100)}% · {_GEN['msg']}")
        else:
            ui_note(row, f"Creando… {int(_GEN['frac'] * 100)}% · {_GEN['msg']}")
        row.operator("cs2.cancel_generate", text="", icon="CANCEL")
        return
    if has and _PENDING[0]:
        lay.label(text="Hay cambios sin aplicar", icon="TIME")
    col = lay.column(align=True)
    col.scale_y = big if has else big - 0.2
    col.alert = bool(has and _PENDING[0])
    col.operator("cs2.generate", text="Actualizar edificio" if has else "Crear el edificio", icon="FILE_REFRESH")


def _draw_footer(lay, s):
    """Pie del panel: interruptor «Avanzado», versión y aviso de versión de Blender."""
    lay.separator()
    row = lay.row(align=True)
    row.prop(s, "studio_adv", text="Mostrar opciones avanzadas", icon="PREFERENCES", toggle=True)
    ui_note(lay, "Avanzado muestra: muro cortina, modelos propios, opciones de horneado, la limpieza de la malla y la ficha con IA.")
    lay.label(text="v" + ".".join(str(v) for v in bl_info["version"]))
    if bpy.app.version < TESTED_BLENDER:
        ui_note(lay, "Probado solo en Blender %d.%d: en tu versión (%s) algo puede fallar." % (TESTED_BLENDER[:2] + (bpy.app.version_string,)), icon="ERROR")


def _draw_tex_update(lay, s, big=1.0):
    """Botón para refrescar las texturas (solo en la pestaña Texturas): reasigna si ya hay texturas, si no actualiza la vista rápida."""
    if s.ui_tab != "TEX":
        return
    st = tex_status(s)
    if not st["built"]:
        return
    if st["assigned"]:
        col = lay.column(align=True)
        col.scale_y = big
        col.operator("cs2.tex_assign", text="Actualizar texturas", icon="TEXTURE")
    elif s.pbr_preview:
        col = lay.column(align=True)
        col.scale_y = big
        col.operator("cs2.preview_update", text="Actualizar vista", icon="TEXTURE")


def _header_draw(self, context):
    """Franja siempre visible en la barra superior de la vista 3D: pestañas con su título y el botón de actualizar."""
    try:
        s = context.scene.cs2_building
    except AttributeError:
        return
    lay = self.layout
    lay.separator()
    row = lay.row(align=True)
    row.prop(s, "ui_tab", expand=True)
    has = _main_obj(s) is not None
    if not has and not _has_shape(s):
        return
    lay.separator()
    row = lay.row(align=True)
    if _GEN["busy"]:
        row.label(text=f"CS2 {int(_GEN['frac'] * 100)}% · {_GEN['msg']}", icon="SORTTIME")
        row.operator("cs2.cancel_generate", text="", icon="CANCEL")
        return
    if _PROG["busy"]:
        row.label(text=f"{_PROG['title']} {int(_PROG['frac'] * 100)}% · {_PROG['msg']}", icon="SORTTIME")
        return
    sub = row.row(align=True)
    sub.alert = bool(has and _PENDING[0])
    sub.operator("cs2.generate", text=("Actualizar edificio" if has else "Crear edificio") + (" •" if has and _PENDING[0] else ""),
                 icon="FILE_REFRESH")
    _draw_tex_update(row, s)


class CS2_PT_panel(bpy.types.Panel):
    """CS2: nombre y resumen del edificio. Las pestañas y el botón de actualizar están en la barra superior de la vista 3D."""
    bl_label = "CS2 · Edificio"
    bl_idname = "CS2_PT_panel"
    bl_options = {"HIDE_HEADER"}
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "CS2"

    def draw(self, context):
        s = context.scene.cs2_building
        lay = self.layout
        lay.label(text="Nombre del asset")
        lay.prop(s, "building_name", text="")
        _draw_resumen(lay, s)


def _split_col(lay):
    """Columna con el título a la izquierda y el campo a la derecha (como las propiedades de Blender)."""
    col = lay.column(align=True)
    col.use_property_split = True
    col.use_property_decorate = False
    return col


class CS2_PT_forma(bpy.types.Panel):
    bl_label = "Lote y forma"
    bl_idname = "CS2_PT_forma"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "CS2"

    poll = _tab_poll("FORMA")

    def draw(self, context):
        s = context.scene.cs2_building
        lay = self.layout
        has_shape = _has_shape(s)
        row = lay.row(align=True)
        row.prop(s, "show_grid", icon="GRID", toggle=True)
        row.operator("cs2.reset", text="Restablecer", icon="TRASH")
        box = lay.box()
        col = _split_col(box)
        manual = (not has_shape) and s.custom_size
        if not has_shape:
            col.prop(s, "custom_size", text="Medidas en metros")
        sub = col.column(align=True)
        sub.enabled = not (has_shape or s.custom_size)       # con una forma o con metros, el lote sale de eso
        sub.prop(s, "cells_x", text="Frente")
        sub.prop(s, "cells_z", text="Fondo")
        if manual:
            col.prop(s, "width", text="Ancho (m)")
            col.prop(s, "depth", text="Largo (m)")
        if not has_shape:
            col.prop(s, "floors", text="Pisos")
        col.prop(s, "floor_height", text="Altura de piso (m)")
        try:
            w, d = _lot_wd(s)
            cx, cz = lot_cells(w, d)
            if has_shape:
                ui_note(box, "El lote sale del tamaño de tu forma.")
            elif manual:
                ui_note(box, "El lote sale de tus medidas en metros.")
            ui_note(box, f"Lote: {cx * CELL:g} x {cz * CELL:g} m · alto {s.floors * s.floor_height:.1f} m" if not has_shape
                    else f"Lote: {cx * CELL:g} x {cz * CELL:g} m")
            warn = lot_warning(w, d)
            if warn:
                box.label(text=warn, icon="ERROR")
        except Exception:
            _log_exc("CS2_PT_forma")
        if not has_shape:
            lay.label(text="Forma del edificio")
            row = lay.row(align=True)
            for kind, label in (("BOX", "Caja"), ("L", "L"), ("T", "T"), ("U", "U")):
                row.operator("cs2.create_shape", text=label).kind = kind
        else:
            lay.prop(s, "shape_object", text="")
            ui_note(lay, f"{len(s.shape_object.data.polygons)} caras · Tab edita, E extruye")
            _draw_shape_tools(lay, context, s)
            row = lay.row(align=True)
            row.prop(s, "curve_deg")
            row.operator("cs2.curve_select", text="", icon="RESTRICT_SELECT_OFF")
        lay.separator()
        _draw_build_button(lay, s)
        if _main_obj(s) is not None:
            row = lay.row(align=True)
            row.operator("ed.undo", text="Deshacer", icon="LOOP_BACK")
            row.operator("cs2.delete_all", text="Borrar todo", icon="TRASH")
        ui_note(lay, "Cuando algo cambie, el botón «Actualizar edificio» también está arriba, en la barra de la vista 3D.")


class CS2_OT_roof_apply(bpy.types.Operator):
    bl_idname = "cs2.roof_apply"
    bl_label = "Aplicar este techo a las caras elegidas"
    bl_description = "En Editar forma: elegí las caras de arriba (techo) y aplicales los ajustes de techo del panel"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        s = context.scene.cs2_building
        obj = _edit_shape_obj(context)
        if obj is None:
            self.report({"ERROR"}, "Entrá en 'Editar forma' y elegí las caras de arriba")
            return {"CANCELLED"}
        bm = bmesh.from_edit_mesh(obj.data)
        lays = wall_layers(bm, create=True)
        faces = [f for f in _selected_faces(bm) if f.normal.z > 0.12]
        if not faces:
            self.report({"ERROR"}, "Elegí al menos una cara que mire hacia arriba (el techo)")
            return {"CANCELLED"}
        vals = roof_values_ui(s)
        for f in faces:
            f[lays["cs2_r_set"]] = 1
            f[lays["cs2_r_type"]] = ROOF_TYPE_ID.get(vals["type"], 1)
            for k in ROOF_KEYS_INT + ROOF_KEYS_FLOAT:
                if k in ("set", "type"):
                    continue
                f[lays["cs2_r_" + k]] = vals[k]
        bmesh.update_edit_mesh(obj.data, loop_triangles=False, destructive=False)
        _on_change(s, context)
        self.report({"INFO"}, f"Techo aplicado a {len(faces)} cara(s)")
        return {"FINISHED"}


class CS2_OT_roof_clear(bpy.types.Operator):
    bl_idname = "cs2.roof_clear"
    bl_label = "Volver al techo general"
    bl_description = "Las caras elegidas vuelven a usar el techo general del panel"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        s = context.scene.cs2_building
        obj = _edit_shape_obj(context)
        if obj is None:
            self.report({"ERROR"}, "Entrá en 'Editar forma' y elegí caras")
            return {"CANCELLED"}
        bm = bmesh.from_edit_mesh(obj.data)
        lays = wall_layers(bm, create=True)
        for f in _selected_faces(bm):
            f[lays["cs2_r_set"]] = 0
        bmesh.update_edit_mesh(obj.data, loop_triangles=False, destructive=False)
        _on_change(s, context)
        return {"FINISHED"}


class CS2_OT_refresh(bpy.types.Operator):
    bl_idname = "cs2.refresh"
    bl_label = "Actualizar"
    bl_description = "Vuelve a armar el edificio con lo que tengas en el panel (sirve también mientras editás la forma)"

    def execute(self, context):
        try:
            regenerate(context, live=True)
        except Exception as e:
            log_error(getattr(self, "bl_idname", "operador"), e)
            self.report({"ERROR"}, str(e))
            return {"CANCELLED"}
        return {"FINISHED"}


LOOK_PRESETS = {
    "WOOD_BLACK": ("Casa moderna: madera y marcos negros", dict(
        facade_color=(0.62, 0.60, 0.56), mat_c_color=(0.012, 0.012, 0.014), mat_d_color=(0.40, 0.17, 0.05),
        wall_mat_g="4", frm_mat_g="4", slt_mode_g="1", slt_gap_g=0.16, slt_w_g=0.07, slt_d_g=0.04, slt_mat_g="5",
        pan_mode_g="1", cas_w_g=0.07, cas_d_g=0.05, cas_mat_g="4", win_w=2.2, win_h=2.2, win_sill=0.25, balconies="none", string_courses=False,
        r_preset="PEAKED_MODERN")),
    "WHITE_BLACK": ("Casa moderna: blanca y marcos negros", dict(
        facade_color=(0.78, 0.78, 0.76), mat_c_color=(0.012, 0.012, 0.014), mat_d_color=(0.40, 0.17, 0.05),
        wall_mat_g="1", frm_mat_g="4", slt_mode_g="0", pan_mode_g="1", cas_w_g=0.08, cas_d_g=0.05, cas_mat_g="4",
        win_w=2.4, win_h=2.2, win_sill=0.2, balconies="none", string_courses=False, r_preset="FLAT_SLAB")),
}


# Tipos de fachada: arquetipos listos (torre de vidrio, bloque de oficinas, ladrillo comercial, departamentos, casa en hilera, depósito...).
# Cada tipo pone varios valores generales de una vez; antes de aplicarlo se vuelve a FACADE_BASE para que no se acumulen restos del anterior.
FACADE_BASE = dict(
    window_style="NONE", door_style="NONE", balcony_style="NONE", balconies="none", balcony_walls="front", string_courses=False,
    win_w=1.3, win_h=1.4, win_sill=0.9, wall_mat_g="1", ground_mat_g="0", frm_mat_g="1", pan_mode_g="1", pan_mat_g="1",
    gr_on_g="0", sh_on_g="0", sh_aw_g="0", slt_mode_g="0", cas_w_g=0.0, cw_on_g=False, r_preset="FLAT",
    facade_color=(0.69, 0.58, 0.41), trim_color=(0.81, 0.77, 0.68), roof_color=(0.15, 0.115, 0.097))
FACADE_TYPES = {
    "GLASS_TOWER": ("Torre de vidrio", "Muro cortina en todas las paredes, techo plano", dict(
        cw_on_g=True, roof_color=(0.05, 0.05, 0.06), r_preset="FLAT"), dict(floors=14, floor_height=3.6)),
    "OFFICE": ("Bloque de oficinas", "Ventanas anchas, losas marcadas y techo plano", dict(
        facade_color=(0.62, 0.64, 0.66), win_w=2.4, win_h=1.7, win_sill=0.9, gr_on_g="3", window_style="Ventana tira horizontal",
        r_preset="FLAT_SLAB"), dict(floors=8, floor_height=3.4)),
    "BRICK_COMMERCIAL": ("Comercial de ladrillo", "Ladrillo, cornisas, vidriera abajo y ventanas con cuarterones", dict(
        wall_mat_g="2", frm_mat_g="1", window_style="Ventana 2x2 cuarterones", string_courses=True, sh_on_g="1", r_preset="FLAT"),
        dict(floors=4, floor_height=3.4)),
    "APARTMENT_SLAB": ("Departamentos (bloque)", "Balcones de vidrio alternados y crujías de color", dict(
        facade_color=(0.78, 0.75, 0.70), balconies="alternate", balcony_style="Balcón vidrio y pasamanos", window_style="Ventana 2 hojas",
        pan_mode_g="6", pan_mat_g="2", r_preset="FLAT"), dict(floors=6, floor_height=3.0)),
    "APARTMENT_TOWER": ("Torre de departamentos", "Balcones corridos continuos y retícula estructural", dict(
        facade_color=(0.80, 0.80, 0.78), balconies="all", balcony_style="Corrido continuo vidrio", gr_on_g="1", window_style="Ventana lisa",
        r_preset="FLAT_SLAB"), dict(floors=12, floor_height=3.0)),
    "TOWNHOUSE": ("Casa en hilera", "Tejado a dos aguas, ventanas con cuarterones y balcón francés", dict(
        facade_color=(0.74, 0.62, 0.50), window_style="Ventana 2x2 cuarterones", door_style="Puerta 4 cuarterones", string_courses=True,
        balconies="alternate", balcony_style="Balcón francés", r_preset="GABLE_TILE"), dict(floors=3, floor_height=3.0)),
    "COTTAGE": ("Casa chica", "Dos pisos, persianas abiertas y chimenea", dict(
        facade_color=(0.85, 0.82, 0.74), window_style="Ventana persianas abiertas", door_style="Puerta 2 cuarterones", r_preset="GABLE_CHIM"),
        dict(floors=2, floor_height=2.9)),
    "SHOPFRONT": ("Local con vivienda", "Vidriera con pilares y toldos abajo, ventanas arriba", dict(
        wall_mat_g="2", sh_on_g="1", sh_aw_g="1", string_courses=True, window_style="Ventana con moldura", r_preset="FLAT"),
        dict(floors=3, floor_height=3.2)),
    "WAREHOUSE": ("Depósito", "Gris, pocas ventanas altas, portón y techo de chapa", dict(
        facade_color=(0.55, 0.55, 0.52), wall_mat_g="3", win_w=1.6, win_h=0.7, win_sill=4.2, window_style="Ventana tira horizontal",
        door_style="Portón de tiras", r_preset="SHED_MODERN"), dict(floors=1, floor_height=6.5)),
    "FACTORY_HALL": ("Nave industrial", "Una sola planta alta, ventanas en tira y techo a dos aguas de chapa", dict(
        facade_color=(0.50, 0.52, 0.50), wall_mat_g="3", win_w=2.6, win_h=0.9, win_sill=5.2, window_style="Ventana tira horizontal",
        door_style="Portón de tiras", r_preset="GABLE_METAL"), dict(floors=1, floor_height=8.0)),
    "INDUSTRIAL_TOWER": ("Torre industrial", "Gris oscuro, ventanas angostas y altas, techo plano", dict(
        facade_color=(0.40, 0.40, 0.42), wall_mat_g="3", window_style="Ventana angosta alta", win_w=0.9, win_h=2.0, win_sill=0.9, r_preset="FLAT"),
        dict(floors=6, floor_height=4.0)),
    "MIXED_USE": ("Uso mixto", "Comercio abajo, departamentos con balcón arriba", dict(
        facade_color=(0.76, 0.70, 0.62), sh_on_g="1", balconies="alternate", balcony_style="Balcón balaustres", window_style="Ventana con moldura",
        string_courses=True, r_preset="PEAKED_MODERN"), dict(floors=5, floor_height=3.1)),
    "CLASSIC": ("Clásico con mansarda", "Cornisa con vuelo, balcones de herrería y techo mansarda", dict(
        facade_color=(0.82, 0.78, 0.68), window_style="Ventana con moldura", balconies="alternate", balcony_style="Balcón francés balaustres",
        string_courses=True, string_style="CORNICE", r_preset="MANSARD"), dict(floors=6, floor_height=3.3)),
}


class CS2_OT_look(bpy.types.Operator):
    bl_idname = "cs2.look"
    bl_label = "Aplicar estilo"
    bl_description = "Ajusta varios valores generales de una sola vez (después podés cambiar cada uno)"
    bl_options = {"REGISTER", "UNDO"}
    look: bpy.props.StringProperty()

    def execute(self, context):
        s = context.scene.cs2_building
        data = LOOK_PRESETS.get(self.look)
        if not data:
            return {"CANCELLED"}
        live = s.live_update
        s.live_update = False
        try:
            for k, v in data[1].items():
                if k == "r_preset":
                    s.r_preset = v
                    _roof_preset_apply(s, context)
                else:
                    setattr(s, k, v)
        finally:
            s.live_update = live
        try:
            regenerate(context, live=True)
        except Exception as e:
            log_error(getattr(self, "bl_idname", "operador"), e)
            self.report({"ERROR"}, str(e))
            return {"CANCELLED"}
        return {"FINISHED"}


class CS2_OT_facade_type(bpy.types.Operator):
    bl_idname = "cs2.facade_type"
    bl_label = "Tipo de fachada"
    bl_description = "Aplica un tipo de fachada completo (estilos, materiales, balcones, techo, colores); después podés cambiar cada valor"
    bl_options = {"REGISTER", "UNDO"}
    kind: bpy.props.StringProperty()

    def execute(self, context):
        s = context.scene.cs2_building
        data = FACADE_TYPES.get(self.kind)
        if not data:
            return {"CANCELLED"}
        live = s.live_update
        s.live_update = False
        try:
            vals = dict(FACADE_BASE)
            vals.update(data[2])
            for k, v in vals.items():
                if k == "r_preset":
                    s.r_preset = v
                    _roof_preset_apply(s, context)
                else:
                    try:
                        setattr(s, k, v)
                    except (TypeError, ValueError):
                        _log_exc("facade_type_" + k)
        finally:
            s.live_update = live
        try:
            regenerate(context, live=True)
        except Exception as e:
            log_error(getattr(self, "bl_idname", "operador"), e)
            self.report({"ERROR"}, str(e))
            return {"CANCELLED"}
        fl = data[3]
        self.report({"INFO"}, f"{data[0]}: pensado para {fl['floors']} pisos de {fl['floor_height']} m (la altura sale de tu forma)")
        return {"FINISHED"}


class CS2_OT_porch(bpy.types.Operator):
    bl_idname = "cs2.porch_preset"
    bl_label = "Modelo de porche"
    bl_description = "Arma un porche de ese modelo (después podés ajustar cada valor)"
    bl_options = {"REGISTER", "UNDO"}
    kind: bpy.props.StringProperty()

    def execute(self, context):
        s = context.scene.cs2_building
        data = PORCH_PRESETS.get(self.kind)
        if not data:
            return {"CANCELLED"}
        vals = dict(PORCH_DEF)
        vals.update(walls=1)
        vals.update(data[1])
        live = s.live_update
        s.live_update = False
        try:
            if s.edit_scope == "ALL":
                s.por_on_g = True
                s.por_walls_g = str(vals["walls"])
                for k in PORCH_ENUMS:
                    setattr(s, "por_" + k + "_g", str(int(vals[k])))
                for k in PORCH_NUMS:
                    setattr(s, "por_" + k + "_g", float(vals[k]))
            else:
                w = context.scene.cs2_wall
                w.use_por = True
                w.por_on = "2"
                for k in PORCH_ENUMS:
                    setattr(w, "por_" + k, str(int(vals[k])))
                for k in PORCH_NUMS:
                    setattr(w, "por_" + k, float(vals[k]))
        finally:
            s.live_update = live
        try:
            regenerate(context, live=True)
        except Exception as e:
            log_error(getattr(self, "bl_idname", "operador"), e)
            self.report({"ERROR"}, str(e))
            return {"CANCELLED"}
        return {"FINISHED"}


def _refresh_btn(lay):
    lay.operator("cs2.refresh", icon="FILE_REFRESH")


def _faces_state(context):
    """(editando, cantidad de caras elegidas) para el alcance 'Caras elegidas'."""
    obj = _edit_shape_obj(context)
    if obj is None:
        return False, 0
    return True, len(_selected_faces(bmesh.from_edit_mesh(obj.data)))


def _draw_scope(lay, context, s):
    """Selector '¿A qué le aplico los cambios?' (todo el edificio o solo las caras elegidas)."""
    lay.label(text="¿A qué le aplico los cambios?")
    lay.prop(s, "edit_scope", expand=True)
    if s.edit_scope == "ALL":
        lay.label(text="Valen para todo el edificio (menos lo que una cara tenga distinto).", icon="HOME")
    else:
        editing, n = _faces_state(context)
        if editing:
            lay.label(text=f"{n} cara(s) elegida(s): el cambio vale solo para ellas.", icon="SNAP_FACE")
        else:
            lay.label(text="Necesitás entrar a Editar forma y elegir caras.", icon="SNAP_FACE")


def _scope_gate(lay, context, s):
    """Dibuja el aviso si el alcance 'Caras elegidas' todavía no se puede usar. True = se puede editar."""
    if s.edit_scope != "FACES":
        return True
    editing, n = _faces_state(context)
    if not editing:
        lay.operator("cs2.edit_shape", text="Entrar a elegir caras", icon="EDITMODE_HLT")
        lay.label(text="Elegí las caras en la forma para editarlas.")
        return False
    if n == 0:
        lay.label(text="Hacé clic en una o más caras de la forma.", icon="RESTRICT_SELECT_OFF")
        return False
    _face_diagnosis(lay, context, s)
    return True


def _face_diagnosis(lay, context, s):
    """Medidas de la cara activa y por qué podría no recibir ventanas o puertas."""
    try:
        obj = _edit_shape_obj(context)
        f = bmesh.from_edit_mesh(obj.data).faces.active
        if f is None or abs(f.normal.z) > 0.12:
            return
        pts = [obj.matrix_world @ v.co for v in f.verts]
        n = Vector((f.normal.x, f.normal.y, 0.0)).normalized()
        u = Vector((0, 0, 1)).cross(n)
        a_ = [p.dot(u) for p in pts]
        z_ = [p.z for p in pts]
        wd, ht = max(a_) - min(a_), max(z_) - min(z_)
        box = lay.box()
        box.label(text=f"Cara activa: {wd:.1f} m de ancho x {ht:.1f} m de alto", icon="INFO")
        if wd < s.win_w + 1.0:
            box.label(text=f"Muy angosta: pide al menos {s.win_w + 1.0:.1f} m para ventanas (salvo que sea parte de una curva).", icon="ERROR")
        if ht < s.floor_height - 0.1:
            box.label(text="Más baja que un piso: puerta y ventanas pueden no entrar.", icon="ERROR")
        if len(f.verts) != 4:
            box.label(text=f"Tiene {len(f.verts)} lados: las aberturas solo entran en tramos rectos.", icon="ERROR")
    except Exception:
        _log_exc("_face_diagnosis")


def _draw_roof_fields(lay, s):
    lay.prop(s, "r_preset", text="Modelo")
    lay.prop(s, "r_type")
    if s.r_type == "FLAT":
        lay.label(text="Borde del techo plano (solo donde no hay una pared más alta)", icon="INFO")
        bx = lay.box()
        bx.prop(s, "r_nopar")
        sub = bx.column(align=True)
        sub.enabled = not s.r_nopar
        sub.prop(s, "r_par_h")
        sub.prop(s, "r_par_t")
        bx.prop(s, "r_nocor")
        sub = bx.column(align=True)
        sub.enabled = not s.r_nocor
        sub.prop(s, "r_cor_h")
        sub.prop(s, "r_cor_o")
        return
    box = lay.box()
    box.label(text="Forma", icon="MOD_BUILD")
    if s.r_type == "MANSARD":
        box.prop(s, "r_pitch", text="Inclinación del faldón (°)")
        box.prop(s, "r_pitch2")
        box.prop(s, "r_brk")
    else:
        box.prop(s, "r_pitch")
    if s.r_type in ("GABLE", "SHED", "BUTTERFLY"):
        box.prop(s, "r_ridge", text="")
    if s.r_type == "GABLE":
        box.prop(s, "r_peak")
    if s.r_type == "SHED":
        box.prop(s, "r_flip")
    box.prop(s, "r_t")
    box = lay.box()
    box.label(text="Aleros", icon="AXIS_TOP")
    box.prop(s, "r_oe")
    if s.r_type in ("GABLE", "SHED", "BUTTERFLY"):
        box.prop(s, "r_ov")
        sub = box.box()
        sub.label(text="Por lado (−1 = igual al general)")
        row = sub.row(align=True)
        row.prop(s, "r_sd_fr")
        row.prop(s, "r_sd_bk")
        row = sub.row(align=True)
        row.prop(s, "r_sd_lf")
        row.prop(s, "r_sd_rg")
    else:
        box.label(text="Aleros por lado: solo en dos aguas, una agua y mariposa", icon="INFO")
    box = lay.box()
    box.label(text="Cubierta", icon="MESH_GRID")
    box.prop(s, "r_ribs")
    if s.r_ribs:
        box.prop(s, "r_rib_gap")
        box.prop(s, "r_rib_h")
    box.prop(s, "r_cap")
    if s.r_cap:
        box.prop(s, "r_capm", text="")
    if s.r_type in ("GABLE", "HIP", "SHED"):
        box = lay.box()
        box.label(text="Buhardillas", icon="MOD_BUILD")
        box.prop(s, "r_dm", text="")
        if s.r_dm != "0":
            box.prop(s, "r_dm_n")
            if s.r_type != "SHED":
                box.prop(s, "r_dm_side", text="")
            box.prop(s, "r_dm_w")
            box.prop(s, "r_dm_h")
            box.prop(s, "r_dm_q")
    box = lay.box()
    box.label(text="Chimeneas", icon="MESH_CUBE")
    box.prop(s, "r_chn")
    if s.r_chn:
        box.prop(s, "r_chpos")
        box.prop(s, "r_chside")
        row = box.row(align=True)
        row.prop(s, "r_chw")
        row.prop(s, "r_chd")
        box.prop(s, "r_chh")
        box.prop(s, "r_chcap", text="")


class CS2_PT_colores(bpy.types.Panel):
    bl_label = "Colores"
    bl_idname = "CS2_PT_colores"
    poll = _tab_poll("PERS")
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "CS2"

    def draw(self, context):
        s = context.scene.cs2_building
        lay = self.layout
        lay.label(text="Siempre valen para todo el edificio.", icon="HOME")
        box = lay.box()
        col = _split_col(box)
        col.prop(s, "facade_color", text="Fachada")
        col.prop(s, "trim_color", text="Molduras")
        col.prop(s, "roof_color", text="Techo")
        col.prop(s, "door_color", text="Puertas")
        col.prop(s, "gar_color", text="Portón de garaje")
        col.prop(s, "rail_color", text="Barandas y rejas")
        box = lay.box()
        box.label(text="Materiales extra (para paños y paredes)")
        col = _split_col(box)
        col.prop(s, "mat_b_color", text="B · ladrillo")
        col.prop(s, "mat_c_color", text="C · gris oscuro")
        col.prop(s, "mat_d_color", text="D · madera")


def _redraw_ui():
    """Fuerza el redibujado: sin esto Blender no repinta durante un operador largo y parece colgado."""
    try:
        bpy.ops.wm.redraw_timer(type="DRAW_WIN_SWAP", iterations=1)
    except Exception:
        pass



def _progress_cb(context, name=""):
    if _PROG["busy"]:        # ya hay una barra de un operador mayor (Rehacer texturas, Exportar…): este paso ocupa su tramo
        a, b = _PROG.get("seg", (0.0, 1.0))
        return lambda f, m="": _prog_step(context, a + (b - a) * max(0.0, min(1.0, f)), m)
    wm = context.window_manager
    wm.progress_begin(0, 100)
    _TITLES = {"uv_generate": "Generando UV", "bake_ao_all": "Horneando sombras", "assign_textures": "Asignando texturas"}
    _PROG.update(name=name, t0=time.time(), last=0.0, busy=True, frac=0.0, msg="Empezando", title=_TITLES.get(name, name))

    def cb(frac, msg):
        _PROG.update(frac=max(0.0, min(1.0, frac)), msg=msg)
        try:
            wm.progress_update(int(max(0.0, min(1.0, frac)) * 100))
            context.workspace.status_text_set(f"{msg} ({int(max(0.0, min(1.0, frac)) * 100)}%)")
        except Exception:
            pass
        now = time.time()
        if now - _PROG["last"] > 0.15:
            _PROG["last"] = now
            _redraw_ui()
    cb(0.0, "Empezando")
    return cb


def _progress_end(context):
    if _PROG["busy"] and not _PROG["name"]:
        return               # la barra es de un operador mayor: la cierra él
    _PROG.update(busy=False, frac=0.0, msg="")
    if _PROG["name"]:
        _TIMINGS[_PROG["name"]] = time.time() - _PROG["t0"]
        _PROG["name"] = ""
    try:
        context.window_manager.progress_end()
        context.workspace.status_text_set(None)
    except Exception:
        pass


class CS2_OT_uv_generate(bpy.types.Operator):
    bl_idname = "cs2.uv_generate"
    bl_label = "Generar UV"
    bl_description = ("Paso 1. Arma el mapa UV de los tres LODs. En el modo con sombras analiza qué caras se ven más, "
                      "reparte los píxeles en consecuencia y comparte las islas iguales")
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        if _main_obj(context.scene.cs2_building) is None:
            cls.poll_message_set("Primero creá el edificio")
            return False
        return True

    def execute(self, context):
        s = context.scene.cs2_building
        p = props_to_params(s)
        objs = _ensure_lods(context, s)
        cb = _progress_cb(context, "uv_generate")
        try:
            infos = uv_generate(context, p, objs, cb)
        except Exception as e:
            log_error(getattr(self, "bl_idname", "operador"), e)
            self.report({"ERROR"}, f"No pude generar las UV: {e}")
            return {"CANCELLED"}
        finally:
            _progress_end(context)
        i0 = infos[0]
        if s.bake_ao:
            self.report({"INFO"}, f"UV listas: {i0['islas']} islas ({i0['compartidas']} compartidas), {i0['px_por_m']} px/m, "
                                  f"{i0['uso']}% del mapa en uso. Siguiente: hornear las sombras")
        else:
            self.report({"INFO"}, "UV listas (atlas con repetición). Siguiente: asignar las texturas")
        return {"FINISHED"}


class CS2_OT_bake_ao(bpy.types.Operator):
    bl_idname = "cs2.bake_ao"
    bl_label = "Hornear sombras"
    bl_description = "Paso 2. Calcula la oclusión ambiental real de cada LOD sobre sus UV (es lo lento; se hace una sola vez)"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        s = context.scene.cs2_building
        st = tex_status(s)
        if not (st["uv"] and st["mode"] == "BAKE"):
            cls.poll_message_set("Primero generá las UV en el modo con sombras horneadas")
            return False
        return True

    def execute(self, context):
        s = context.scene.cs2_building
        p = props_to_params(s)
        objs = _ensure_lods(context, s)
        cb = _progress_cb(context, "bake_ao_all")
        try:
            bake_ao_all(context, p, objs, cb)
        except Exception as e:
            log_error(getattr(self, "bl_idname", "operador"), e)
            self.report({"ERROR"}, f"No pude hornear: {e}")
            return {"CANCELLED"}
        finally:
            _progress_end(context)
        self.report({"INFO"}, "Sombras horneadas. Siguiente: asignar las texturas")
        return {"FINISHED"}


class CS2_OT_tex_assign(bpy.types.Operator):
    bl_idname = "cs2.tex_assign"
    bl_label = "Asignar texturas"
    bl_description = "Paso 3. Aplica los materiales (colores y mapas PBR) a esas UV y los muestra en el modelo. Se puede repetir sin volver a hornear"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        st = tex_status(context.scene.cs2_building)
        if not st["uv"]:
            cls.poll_message_set("Primero generá las UV")
            return False
        if st["mode"] == "BAKE" and not st["bake"]:
            cls.poll_message_set("Primero horneá las sombras")
            return False
        return True

    def execute(self, context):
        s = context.scene.cs2_building
        p = props_to_params(s)
        objs = _ensure_lods(context, s)
        cb = _progress_cb(context, "assign_textures")
        try:
            assign_textures(context, p, objs, cb)
        except Exception as e:
            log_error(getattr(self, "bl_idname", "operador"), e)
            self.report({"ERROR"}, f"No pude asignar las texturas: {e}")
            return {"CANCELLED"}
        finally:
            _progress_end(context)
        probs = texture_problems(s)
        if probs:
            self.report({"WARNING"}, "Texturas asignadas, pero " + texture_problem_text(probs))
        else:
            self.report({"INFO"}, "Texturas asignadas al modelo")
        return {"FINISHED"}


class CS2_OT_retexture(bpy.types.Operator):
    bl_idname = "cs2.retexture"
    bl_label = "Rehacer texturas"
    bl_description = "Genera las UV, hornea las sombras (si el modo las usa) y asigna las texturas, todo seguido"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        if _main_obj(context.scene.cs2_building) is None:
            cls.poll_message_set("Primero creá el edificio")
            return False
        return True

    @_with_work("Re-texturizando")
    def execute(self, context):
        s = context.scene.cs2_building
        steps = [bpy.ops.cs2.uv_generate]
        if s.bake_ao:
            steps.append(bpy.ops.cs2.bake_ao)
        steps.append(bpy.ops.cs2.tex_assign)
        for k_, step in enumerate(steps):
            _PROG["seg"] = (k_ / len(steps), (k_ + 1) / len(steps))
            if "FINISHED" not in step():
                self.report({"ERROR"}, "Se cortó antes de terminar: mirá el mensaje anterior o copiá el informe")
                return {"CANCELLED"}
        self.report({"INFO"}, "Texturas rehechas")
        return {"FINISHED"}


class CS2_OT_copy_report(bpy.types.Operator):
    bl_idname = "cs2.copy_report"
    bl_label = "Copiar informe"
    bl_description = ("Copia al portapapeles un informe con las versiones, los ajustes, el estado de las UV y los errores recientes. "
                      "Pégalo en el chat si algo falla")

    def execute(self, context):
        txt = build_report()
        context.window_manager.clipboard = txt
        print(txt)
        n = len(_ERRLOG)
        self.report({"INFO"}, "Informe copiado" + (f" ({n} error{'es' if n != 1 else ''} registrado{'s' if n != 1 else ''})" if n else " (sin errores)"))
        return {"FINISHED"}


class CS2_OT_curve_select(bpy.types.Operator):
    bl_idname = "cs2.curve_select"
    bl_label = "Ver curvas detectadas"
    bl_description = "Elige en la forma las paredes que se tratan como curvas (según el ángulo de «Unir curvas hasta»), para comprobar qué se unió"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        s = context.scene.cs2_building
        obj = s.shape_object
        if obj is None or obj.type != "MESH":
            self.report({"WARNING"}, "Primero elegí o creá una forma")
            return {"CANCELLED"}
        if obj.mode != "EDIT":
            bpy.ops.cs2.edit_shape()
        bm = bmesh.from_edit_mesh(obj.data)
        lays = wall_layers(bm)
        walls, faces = [], []
        for f in bm.faces:
            if abs(f.normal.z) < 0.12:
                nh = Vector((f.normal.x, f.normal.y, 0.0))
                if nh.length > 1e-6:
                    walls.append(([v.co.copy() for v in f.verts], nh.normalized(), face_cfg(f, lays)))
                    faces.append(f)
        chains = curved_chains(walls, s.curve_deg)
        for f in bm.faces:
            f.select_set(False)
        for ch in chains:
            for i in ch:
                faces[i].select_set(True)
        bm.select_flush_mode()
        bmesh.update_edit_mesh(obj.data, loop_triangles=False, destructive=False)
        n = sum(len(c) for c in chains)
        self.report({"INFO"}, f"{len(chains)} curva(s) con {n} paredes" if chains else "No se detectó ninguna curva con ese ángulo")
        return {"FINISHED"}


class CS2_OT_sel_wall_mat(bpy.types.Operator):
    bl_idname = "cs2.sel_wall_mat"
    bl_label = "Elegir paredes por material"
    bl_description = "Agrega a la selección todas las paredes de la forma que tienen ese material (A, B, C o D); sirve para cambiarlas juntas"
    bl_options = {"REGISTER", "UNDO"}
    mat: StringProperty(default="1")

    def execute(self, context):
        obj = _edit_shape_obj(context)
        if obj is None:
            self.report({"WARNING"}, "Primero entrá a elegir caras")
            return {"CANCELLED"}
        bm = bmesh.from_edit_mesh(obj.data)
        lays = wall_layers(bm)
        lay = lays.get("cs2_f_mat")
        want = int(self.mat)
        n = 0
        for f in bm.faces:
            if abs(f.normal.z) > 0.12:
                continue
            if (int(f[lay]) if lay is not None else 0) == want:
                f.select_set(True)
                n += 1
        bm.select_flush_mode()
        bmesh.update_edit_mesh(obj.data, loop_triangles=False, destructive=False)
        if not n:
            self.report({"INFO"}, "Ninguna pared tiene ese material")
        return {"FINISHED"}


class CS2_OT_slot_rot(bpy.types.Operator):
    bl_idname = "cs2.slot_rot"
    bl_label = "Girar textura"
    bl_description = "Gira la textura del material (0 = volver al giro original). Se ve al actualizar el edificio"
    bl_options = {"REGISTER", "UNDO"}
    slot: StringProperty(default="facade")
    delta: FloatProperty(default=90.0)

    def execute(self, context):
        s = context.scene.cs2_building
        name = f"ms_{self.slot}_rot"
        if self.slot not in PBR_SLOTS or not hasattr(s, name):
            return {"CANCELLED"}
        cur = getattr(s, name)
        new = 0.0 if self.delta == 0.0 else ((cur + self.delta + 180.0) % 360.0) - 180.0
        setattr(s, name, new)
        return {"FINISHED"}


class CS2_OT_tex_clear(bpy.types.Operator):
    bl_idname = "cs2.tex_clear"
    bl_label = "Sacar texturas"
    bl_description = "Vuelve el modelo a colores lisos (no borra las UV ni las sombras)"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return _main_obj(context.scene.cs2_building) is not None

    def execute(self, context):
        s = context.scene.cs2_building
        clear_texture_view(_existing_lods(s), props_to_params(s))
        return {"FINISHED"}


class CS2_OT_preview_update(bpy.types.Operator):
    bl_idname = "cs2.preview_update"
    bl_label = "Actualizar vista"
    bl_description = "Pone en el modelo las texturas elegidas (vista rápida, sin UV ni horneado). Si había texturas horneadas asignadas, las reemplaza"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        if _main_obj(context.scene.cs2_building) is None:
            cls.poll_message_set("Primero creá el edificio")
            return False
        return True

    @_with_work("Actualizando vista")
    def execute(self, context):
        s = context.scene.cs2_building
        objs = _existing_lods(s)
        p = props_to_params(s)
        try:
            if any(m is not None and m.get("cs2_orig") for ob in objs for m in ob.data.materials):
                clear_texture_view(objs, p)
            update_preview(dict(p, pbr_preview=True))
        except Exception as e:
            log_error(getattr(self, "bl_idname", "operador"), e)
            self.report({"ERROR"}, f"No pude actualizar la vista: {e}")
            return {"CANCELLED"}
        probs = texture_problems(s)
        if probs:
            self.report({"WARNING"}, "Vista actualizada, pero " + texture_problem_text(probs))
        return {"FINISHED"}


def _uv_lod0(context):
    s = context.scene.cs2_building
    ob0 = _main_obj(s)
    return (s, ob0) if ob0 is not None else (s, None)


def _sel_get(ob):
    """Índices de las caras seleccionadas (en modo objeto o en modo edición)."""
    if ob.mode == "EDIT":
        bm = bmesh.from_edit_mesh(ob.data)
        return {f_.index for f_ in bm.faces if f_.select}
    return {p_.index for p_ in ob.data.polygons if p_.select}


def _sel_set(ob, idx):
    me = ob.data
    if ob.mode == "EDIT":
        bm = bmesh.from_edit_mesh(me)
        bm.faces.ensure_lookup_table()
        for f_ in bm.faces:
            f_.select_set(False)
        for i in idx:
            bm.faces[i].select_set(True)
        bm.select_flush_mode()
        bmesh.update_edit_mesh(me)
        return
    for coll in (me.vertices, me.edges, me.polygons):
        for e in coll:
            e.select = False
    for i in idx:
        poly = me.polygons[i]
        poly.select = True
        for v in poly.vertices:
            me.vertices[v].select = True
    keyset = {tuple(sorted(me.polygons[i].edge_keys[k])) for i in idx for k in range(len(me.polygons[i].edge_keys))}
    for e in me.edges:
        if tuple(sorted(e.vertices)) in keyset:
            e.select = True


class CS2_OT_uv_zone(bpy.types.Operator):
    bl_idname = "cs2.uv_zone"
    bl_label = "Seleccionar zona"
    bl_description = "Selecciona en el LOD0 las caras de una zona, para verlas en el editor de UV, moverlas a mano o fijarlas"
    bl_options = {"REGISTER", "UNDO"}
    zone: EnumProperty(name="Zona", items=[("GROUND", "Planta baja", ""), ("FRONT", "Fachada del frente", ""),
                                           ("DOORS", "Puertas y portones", ""), ("PINNED", "Fijadas", ""), ("ALL", "Todo", "")])

    @classmethod
    def poll(cls, context):
        return _uv_lod0(context)[1] is not None

    def execute(self, context):
        s, ob = _uv_lod0(context)
        me = ob.data
        if ob.mode == "EDIT":
            ob.update_from_editmode()
        front = uv_front_dir(me)
        fh = float(s.floor_height)
        idx = set()
        if self.zone == "PINNED":
            idx = {pl.index for pl in uv_pins_match(me, uv_pins_load(s.uv_pins)).values()}
        else:
            for poly in me.polygons:
                if poly.material_index in SUB_MATS:
                    continue
                tg = uv_zone_tags(poly, front, fh)
                if self.zone == "ALL" or {"GROUND": "ground", "FRONT": "front", "DOORS": "door"}[self.zone] in tg:
                    idx.add(poly.index)
        _sel_set(ob, idx)
        self.report({"INFO"}, f"{len(idx)} caras seleccionadas")
        return {"FINISHED"}


class CS2_OT_uv_pin(bpy.types.Operator):
    bl_idname = "cs2.uv_pin"
    bl_label = "Fijar zonas"
    bl_description = ("Guarda las UV de las caras seleccionadas del LOD0: al regenerar el mapa quedan exactamente donde están "
                      "(también si las moviste a mano en el editor de UV) y el resto se acomoda alrededor")
    bl_options = {"REGISTER", "UNDO"}
    action: EnumProperty(name="Acción", items=[("ADD", "Fijar", ""), ("REMOVE", "Soltar", ""), ("CLEAR", "Soltar todo", "")])

    @classmethod
    def poll(cls, context):
        s, ob = _uv_lod0(context)
        if ob is None:
            cls.poll_message_set("Primero creá el edificio")
            return False
        return True

    def execute(self, context):
        s, ob = _uv_lod0(context)
        me = ob.data
        if self.action == "CLEAR":
            n = len(uv_pins_load(s.uv_pins))
            s.uv_pins = ""
            self.report({"INFO"}, f"Se soltaron {n} caras")
            return {"FINISHED"}
        if ob.mode == "EDIT":
            ob.update_from_editmode()
        sel = _sel_get(ob)
        if not sel:
            self.report({"WARNING"}, "No hay caras seleccionadas en el LOD0")
            return {"CANCELLED"}
        old = uv_pins_load(s.uv_pins)
        match = uv_pins_match(me, old)
        if self.action == "REMOVE":
            keep_old = [k for k in range(len(old)) if not (k in match and match[k].index in sel)]
            new = [old[k] for k in keep_old]
            remap = {k: i for i, k in enumerate(keep_old)}
            for e in new:
                e["r"] = remap.get(e.get("r"), None)
            for i, e in enumerate(new):
                if e["r"] is None:
                    e["r"] = i
            s.uv_pins = json.dumps(new, separators=(",", ":")) if new else ""
            self.report({"INFO"}, f"Quedan {len(new)} caras fijadas")
            return {"FINISHED"}
        if "CS2_unique" not in me.uv_layers or uv_state(me) is None:
            self.report({"ERROR"}, "Primero generá el mapa UV (modo con sombras horneadas)")
            return {"CANCELLED"}
        uvd = me.uv_layers["CS2_unique"].data
        reps = list(me.get("cs2_reps") or range(len(me.polygons)))
        want = set(sel)
        want |= {reps[i] for i in sel if i < len(reps)}
        want = {i for i in want if me.polygons[i].material_index not in SUB_MATS}
        keep_old = [k for k in range(len(old)) if not (k in match and match[k].index in want)]
        entries = [old[k] for k in keep_old]
        remap = {k: i for i, k in enumerate(keep_old)}
        for i, e in enumerate(entries):
            e["r"] = remap.get(e.get("r"), i)
        base = len(entries)
        order = sorted(want)
        where = {pi: base + j for j, pi in enumerate(order)}
        for pi in order:
            poly = me.polygons[pi]
            c = poly.center
            vs = []
            for li in poly.loop_indices:
                co = me.vertices[me.loops[li].vertex_index].co
                u, v = uvd[li].uv
                vs.append([round(co.x, 4), round(co.y, 4), round(co.z, 4), round(u, 5), round(v, 5)])
            r = reps[pi] if pi < len(reps) else pi
            entries.append({"m": poly.material_index, "c": [round(c.x, 4), round(c.y, 4), round(c.z, 4)], "v": vs,
                            "r": where.get(r, where[pi])})
        s.uv_pins = json.dumps(entries, separators=(",", ":"))
        self.report({"INFO"}, f"{len(order)} caras fijadas ({len(entries)} en total)")
        return {"FINISHED"}


class CS2_OT_uv_view(bpy.types.Operator):
    bl_idname = "cs2.uv_view"
    bl_label = "Abrir el editor de UV"
    bl_description = ("Pasa al espacio de trabajo «UV Editing» con el LOD0 en modo edición y las UV del generador activas. "
                      "Ahí se ven y se mueven las islas; volvé con la pestaña «Layout»")
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        s, ob = _uv_lod0(context)
        if ob is None:
            cls.poll_message_set("Primero creá el edificio")
            return False
        if "CS2_unique" not in ob.data.uv_layers:
            cls.poll_message_set("Primero generá el mapa UV (modo con sombras horneadas)")
            return False
        return True

    def execute(self, context):
        s, ob = _uv_lod0(context)
        me = ob.data
        if context.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")
        for o in list(context.selected_objects):
            o.select_set(False)
        ob.select_set(True)
        context.view_layer.objects.active = ob
        me.uv_layers.active = me.uv_layers["CS2_unique"]
        if not _sel_get(ob):
            _sel_set(ob, {p_.index for p_ in me.polygons if p_.material_index not in SUB_MATS})
        ws = bpy.data.workspaces.get("UV Editing")
        if ws is not None and context.window is not None:
            context.window.workspace = ws
            self.report({"INFO"}, "Editor de UV abierto. Seleccioná zonas en la vista 3D, movelas en el editor y volvé a «Fijar» desde el panel CS2")
        else:
            bpy.ops.object.mode_set(mode="EDIT")
            self.report({"INFO"}, "Estás en modo edición con las UV del generador activas: cambiá un área a «Editor de UV»")
        return {"FINISHED"}


class CS2_OT_atlas_view(bpy.types.Operator):
    bl_idname = "cs2.atlas_view"
    bl_label = "Ver atlas de UV"
    bl_description = "Muestra en el editor de imágenes cómo se reparten los materiales en las UV (0 a 1)"

    def execute(self, context):
        s = context.scene.cs2_building
        p = props_to_params(s)
        size = 512
        arr = texture_arrays(p, size)["BaseColor"]
        name = "CS2_Atlas_UV"
        img = bpy.data.images.get(name)
        if img is None or img.size[0] != size:
            if img is not None:
                bpy.data.images.remove(img)
            img = bpy.data.images.new(name, size, size, alpha=True)
        img.pixels.foreach_set(arr.ravel())
        img.update()
        shown = False
        for win in context.window_manager.windows:
            for area in win.screen.areas:
                if area.type in ("IMAGE_EDITOR", "UV"):
                    area.spaces.active.image = img
                    area.tag_redraw()
                    shown = True
        self.report({"INFO"}, "Atlas en el editor de imágenes" if shown else
                    "Imagen 'CS2_Atlas_UV' creada: abrila en un editor de imágenes o de UV")
        return {"FINISHED"}


class CS2_OT_make_textures(bpy.types.Operator):
    bl_idname = "cs2.make_textures"
    bl_label = "Crear texturas"
    bl_description = "Escribe los PNG del juego (BaseColor, ControlMask, MaskMap, Normal, Emissive) en la carpeta del asset"

    @_with_work("Creando texturas")
    def execute(self, context):
        s = context.scene.cs2_building
        out = export_dir_for(s, self)
        if out is None:
            return {"CANCELLED"}
        w, d = load_shape_for(s)["size"].xy
        asset = asset_export_name(s.theme, level_for(s), s.building_name or "Residencial01", w, d)
        folder = os.path.join(out, "ProjectFiles", asset)
        p = props_to_params(s)
        if (p.get("tex_share_asset") or "").strip():
            self.report({"WARNING"}, "Compartís texturas con otro asset: no se escriben texturas propias")
            return {"CANCELLED"}
        if p.get("bake_ao"):
            name = s.building_name or "Residencial01"
            objs = [bpy.data.objects.get(f"{name}_LOD{i}") for i in range(3)]
            if objs[0] is None:
                self.report({"ERROR"}, "Primero generá el edificio")
                return {"CANCELLED"}
            ensure_textures(context, p, objs, 0.0, 0.4)
            _prog_step(context, 0.4, "Armando las texturas")
            arrays = texture_arrays(p, int(s.tex_size))
            sizes = bake_lod_sizes(int(s.tex_size), p)
            paths = []
            if tex_shared(p):
                texs_all, ums = bake_all_lods(context, p, objs, arrays, name, 0.45, 0.95)
                for um in ums:
                    bpy.data.meshes.remove(um)
                paths += write_arrays(folder, asset, texs_all[0], sizes[0])
                if len(texs_all) > 1:
                    paths += write_arrays(folder, f"{asset}_LOD2", texs_all[1], sizes[2])
                write_shared_settings(folder, asset, dict(p, tex_share_asset=""), [1])
                self.report({"INFO"}, f"{len(paths)} texturas (LOD0 y LOD1 comparten) + settings.json en {folder}")
                return {"FINISHED"}
            for lod, ob in enumerate(objs):
                if ob is None:
                    continue
                _prog_step(context, 0.45 + 0.5 * lod / 3.0, f"Horneando LOD{lod}")
                d_ = uv_state(ob.data)
                tex, umesh = bake_lod(context, ob.data, p, arrays, sizes[lod],
                                      ao=read_ao(name, lod, d_["uid"] if d_ else None), have_uv=True)
                bpy.data.meshes.remove(umesh)
                paths += write_arrays(folder, asset if lod == 0 else f"{asset}_LOD{lod}", tex, sizes[lod])
            self.report({"INFO"}, f"{len(paths)} texturas horneadas en {folder}")
            return {"FINISHED"}
        paths = write_textures(folder, asset, p, int(s.tex_size))
        write_shared_settings(folder, asset, p, [1, 2])
        self.report({"INFO"}, f"{len(paths)} texturas en {folder}")
        return {"FINISHED"}


def _pbr_items(self, context):
    global _PBR_ITEMS
    root = context.scene.cs2_building.pbr_library
    items = [(rel, lab, rel) for rel, lab in scan_pbr_library(root)]
    _PBR_ITEMS = items or [("", "(sin sets: elegí la carpeta de la biblioteca)", "")]
    return _PBR_ITEMS


class CS2_OT_pbr_pick(bpy.types.Operator):
    bl_idname = "cs2.pbr_pick"
    bl_label = "Buscar en la biblioteca"
    bl_description = "Elegí un set (difuso, normal, rugosidad...) de la carpeta de la biblioteca para este material"
    bl_property = "choice"
    slot: StringProperty()
    choice: EnumProperty(name="Set", items=_pbr_items)

    def execute(self, context):
        s = context.scene.cs2_building
        if not self.choice:
            self.report({"WARNING"}, "Elegí primero la carpeta de la biblioteca")
            return {"CANCELLED"}
        folder = os.path.join(bpy.path.abspath(s.pbr_library), *self.choice.split("/"))
        n = apply_pbr_set(s, self.slot, folder)
        _on_change(s, context)
        self.report({"INFO"} if n else {"WARNING"}, f"{n} mapas cargados de {self.choice}")
        return {"FINISHED"}

    def invoke(self, context, event):
        context.window_manager.invoke_search_popup(self)
        return {"RUNNING_MODAL"}


class CS2_OT_pbr_folder(bpy.types.Operator):
    bl_idname = "cs2.pbr_folder"
    bl_label = "Cargar carpeta"
    bl_description = "Carga los mapas de una carpeta (busca _Color, _NormalGL, _Roughness, _Metalness, _AmbientOcclusion...)"
    slot: StringProperty()
    directory: StringProperty(subtype="DIR_PATH")
    filter_folder: BoolProperty(default=True, options={"HIDDEN"})

    def execute(self, context):
        s = context.scene.cs2_building
        n = apply_pbr_set(s, self.slot, self.directory)
        _on_change(s, context)
        self.report({"INFO"} if n else {"WARNING"}, f"{n} mapas encontrados")
        return {"FINISHED"}

    def invoke(self, context, event):
        context.window_manager.fileselect_add(self)
        return {"RUNNING_MODAL"}


class CS2_OT_pbr_clear(bpy.types.Operator):
    bl_idname = "cs2.pbr_clear"
    bl_label = "Quitar texturas"
    bl_description = "Vuelve este material a color liso"
    slot: StringProperty()

    def execute(self, context):
        s = context.scene.cs2_building
        live = s.live_update
        s.live_update = False
        try:
            for k, _l in PBR_MAP_FIELDS:
                setattr(s, f"ms_{self.slot}_{k}", "")
            setattr(s, f"ms_{self.slot}_set", "")
            setattr(s, f"ms_{self.slot}_mode", "FLAT")
        finally:
            s.live_update = live
        _on_change(s, context)
        return {"FINISHED"}


class CS2_PT_mats(bpy.types.Panel):
    bl_label = "1 · Materiales"
    bl_idname = "CS2_PT_mats"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "CS2"

    poll = _tab_poll("TEX")

    def draw(self, context):
        s = context.scene.cs2_building
        lay = self.layout
        if _main_obj(s) is not None:
            box = lay.box()
            row = box.row(align=True)
            row.use_property_split = False
            row.prop(s, "pbr_preview", text="Vista rápida", icon="HIDE_OFF" if s.pbr_preview else "HIDE_ON", toggle=True)
            row.operator("cs2.preview_update", text="Actualizar vista", icon="FILE_REFRESH")
            _draw_work_bar(box)
            if s.pbr_preview:
                box.prop(s, "pbr_prev_size", text="Calidad")
                ui_note(box, "Cada textura que elegís se ve enseguida en el modelo.")
            else:
                ui_note(box, "Apagada: el modelo se ve con colores lisos.")
        probs = texture_problems(s)
        if probs:
            warn = lay.box()
            warn.alert = True
            warn.label(text=f"Hay {len(probs)} problemas con las texturas", icon="ERROR")
            for m_, lab_, path_, why_ in probs[:8]:
                warn.label(text=f"{m_} · {lab_}: {why_}")
            if len(probs) > 8:
                warn.label(text=f"… y {len(probs) - 8} más")
        lay.prop(s, "pbr_library")
        box = lay.box()
        box.label(text="Biblioteca de materiales", icon="MATERIAL")
        box.prop(s, "pbr_target")
        if s.pbr_library:
            box.prop(s, "pbr_folder")
            box.template_icon_view(s, "pbr_grid", show_labels=True, scale=7.0, scale_popup=7.0)
        else:
            box.label(text="Elegí la carpeta de la biblioteca (arriba)")
        for sl in PBR_SLOTS:
            box = lay.box()
            row = box.row()
            row.use_property_split = False
            ex = getattr(s, f"ms_{sl}_expanded")
            row.prop(s, f"ms_{sl}_expanded", text="", icon="TRIA_DOWN" if ex else "TRIA_RIGHT", emboss=False)
            row.label(text=PBR_SLOT_LABEL[sl])
            mode = getattr(s, f"ms_{sl}_mode")
            nm = getattr(s, f"ms_{sl}_set")
            ico = mat_icon(getattr(s, f"ms_{sl}_img_color")) if mode == "PBR" else 0
            if ico:
                row.template_icon(icon_value=ico, scale=1.4)
            row.label(text=(nm or "texturas") if mode == "PBR" else "color liso")
            if any(p_[0] == PBR_SLOT_LABEL[sl] for p_ in probs):
                row.label(text="", icon="ERROR")
            if not ex:
                continue
            if ico:
                big = box.row()
                big.template_icon(icon_value=ico, scale=6.0)
                col = big.column(align=True)
                for k, lab in PBR_MAP_FIELDS:
                    path = getattr(s, f"ms_{sl}_{k}")
                    col.label(text=("✔ " if path else "– ") + lab)
            box.prop(s, f"ms_{sl}_mode", expand=True)
            r = box.row(align=True)
            op = r.operator("cs2.pbr_pick", icon="VIEWZOOM")
            op.slot = sl
            op = r.operator("cs2.pbr_folder", text="Carpeta", icon="FILE_FOLDER")
            op.slot = sl
            if mode == "PBR":
                op = r.operator("cs2.pbr_clear", text="", icon="X")
                op.slot = sl
                for k, _l in PBR_MAP_FIELDS:
                    box.prop(s, f"ms_{sl}_{k}")
                box.prop(s, f"ms_{sl}_tint")
                box.prop(s, f"ms_{sl}_tile")
                rr = box.row(align=True)
                rr.prop(s, f"ms_{sl}_rot")
                for lab_, d_ in (("-90°", -90.0), ("+90°", 90.0)):
                    op = rr.operator("cs2.slot_rot", text=lab_)
                    op.slot, op.delta = sl, d_
                op = rr.operator("cs2.slot_rot", text="", icon="X")
                op.slot, op.delta = sl, 0.0
                box.prop(s, f"ms_{sl}_nstr")
                box.prop(s, f"ms_{sl}_flipg")
                box.prop(s, f"ms_{sl}_gmode")
                box.prop(s, f"ms_{sl}_gmult")
                box.prop(s, f"ms_{sl}_aostr")
            box.prop(s, f"ms_{sl}_metal")
            if mode != "PBR":
                box.prop(s, f"ms_{sl}_gloss")
            box.prop(s, f"ms_{sl}_coat")
            box.prop(s, f"ms_{sl}_emis_col")
            box.prop(s, f"ms_{sl}_emis_str")
            box.prop(s, f"ms_{sl}_cmask")
            box.prop(s, f"ms_{sl}_snow")


class CS2_PT_tex(bpy.types.Panel):
    bl_label = "Modo y calidad"
    bl_idname = "CS2_PT_tex"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "CS2"

    poll = _tab_poll("TEX")

    def draw(self, context):
        s = context.scene.cs2_building
        lay = self.layout
        st = tex_status(s)
        if not st["built"]:
            lay.label(text="Primero creá el edificio: la geometría va antes.", icon="INFO")
        lay.prop(s, "tex_mode", expand=True)
        lay.prop(s, "tex_size", text="Tamaño")
        lay.prop(s, "win_rooms", text="Ventanas de noche")
        lay.prop(s, "roof_geo_tiles", text="Tejas en geometría")
        if st["stale"]:
            row = lay.row()
            row.alert = True
            row.label(text="Cambió la geometría o el modo: generá las UV otra vez.", icon="ERROR")
        ui_note(lay, "Pasos: 1 · Materiales, 2 · Mapa UV, 3 · Hornear y asignar.")


class CS2_PT_uv(bpy.types.Panel):
    bl_label = "2 · Mapa UV"
    bl_idname = "CS2_PT_uv"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "CS2"

    poll = _tab_poll("TEX")

    def draw(self, context):
        s = context.scene.cs2_building
        lay = self.layout
        st = tex_status(s)
        if not st["built"]:
            lay.label(text="Primero creá el edificio.", icon="INFO")
            return
        info = st["info"] or {}
        bake = st["mode"] == "BAKE"
        box = lay.box()
        box.label(text="UV generadas" if st["uv"] else "Todavía sin UV", icon="CHECKMARK" if st["uv"] else "UV")
        if not st["lods"]:
            ui_note(box, "Faltan el LOD1 y el LOD2: se generan solos al armar las UV.")
        col = box.column()
        col.scale_y = 1.25
        col.operator("cs2.uv_generate", text="Regenerar UV" if st["uv"] else "Generar UV", icon="UV_DATA")
        _draw_work_bar(box)
        if st["uv"] and info:
            if bake:
                ui_note(box, f"{info.get('islas', 0)} islas de {info.get('caras', 0)} caras ({info.get('compartidas', 0)} compartidas)")
                ui_note(box, f"{info.get('comp_px', {}).get('wall', info.get('px_por_m', 0))} px/m en paredes · {info.get('uso', 0)}% del mapa en uso")
                ppm_ = float(info.get("px_por_m", 0))
                if ppm_ < 60 and int(s.tex_size) < 4096:
                    nxt_ = int(s.tex_size) * 2
                    box.label(text=f"Nitidez baja: con textura {nxt_} sería ~{round(ppm_ * 2)} px/m", icon="INFO")
                if info.get("ocultas"):
                    box.label(text=f"{info['ocultas']} caras interiores casi sin lugar")
            else:
                ui_note(box, f"{info.get('px_por_m', 0)} px/m · {info.get('uso', 0)}% del atlas usado por los materiales")
        row = lay.row(align=True)
        row.operator("cs2.atlas_view", icon="IMAGE_DATA")
        row.operator("cs2.uv_view", icon="UV_FACESEL")
        if bake:
            box = lay.box()
            box.label(text="Reparto del mapa", icon="UV_ISLANDSEL")
            box.prop(s, "uv_smart")
            box.prop(s, "uv_same_density")
            box.prop(s, "uv_grid")
            box.prop(s, "uv_group")
            box = lay.box()
            box.label(text="Ocupación en la textura", icon="FULLSCREEN_ENTER")
            comp_pct = info.get("comp_pct", {}) if st["uv"] else {}
            comp_px = info.get("comp_px", {}) if st["uv"] else {}
            lim = set(info.get("comp_limitado", [])) if st["uv"] else set()
            col = box.column(align=True)
            for k in UV_COMPONENTS:
                col.prop(s, "uv_c_" + k, slider=True)
                if k in comp_pct:
                    sub = col.row(align=True)
                    sub.alignment = "RIGHT"
                    sub.label(text=f"{comp_pct[k]:.0f} % · {comp_px.get(k, 0):.0f} px/m", icon="ERROR" if k in lim else "NONE")
            if lim:
                ui_note(box, "Con el icono de aviso: no llega a la nitidez pedida porque una de sus piezas ya ocupa todo el ancho del mapa.", icon="ERROR")
            ui_note(box, "Más de 1 = las UV de ese componente ocupan más lugar en la textura y se ven con más nitidez (px/m). Dentro de un componente todas las caras quedan igual de nítidas. Se aplica al regenerar las UV.")
        if bake and s.studio_adv:
            box = lay.box()
            box.label(text="Compartir islas", icon="LINKED")
            box.prop(s, "bake_share")
            if s.bake_share:
                box.prop(s, "bake_align")
                box.prop(s, "bake_share_loose")
                box.prop(s, "bake_share_area")
            box.prop(s, "bake_roof_wt")
        if not bake:
            ui_note(lay, "Modo rápido: cada material ocupa una zona del atlas (0 a 1) y su textura se repite dentro.")


class CS2_PT_bake(bpy.types.Panel):
    bl_label = "3 · Hornear y asignar"
    bl_idname = "CS2_PT_bake"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "CS2"

    poll = _tab_poll("TEX")

    def draw(self, context):
        s = context.scene.cs2_building
        lay = self.layout
        st = tex_status(s)
        if not st["built"]:
            lay.label(text="Primero creá el edificio.", icon="INFO")
            return
        bake = st["mode"] == "BAKE"
        ready = st["uv"] and (st["bake"] or not bake)
        if bake:
            box = lay.box()
            box.label(text="Sombras horneadas" if st["bake"] else "Sombras sin hornear", icon="CHECKMARK" if st["bake"] else "LIGHT")
            col = box.column()
            col.enabled = st["uv"]
            col.scale_y = 1.25
            col.operator("cs2.bake_ao", text="Volver a hornear" if st["bake"] else "Hornear sombras", icon="LIGHT")
            _draw_work_bar(box)
            if not st["uv"]:
                ui_note(box, "Primero generá el mapa UV.")
            elif st["bake"]:
                ui_note(box, "Listo: podés cambiar texturas sin hornear de nuevo.")
        box = lay.box()
        box.label(text="Texturas asignadas" if st["assigned"] else "Texturas sin asignar", icon="CHECKMARK" if st["assigned"] else "MATERIAL")
        col = box.column()
        col.enabled = ready
        col.scale_y = 1.4
        col.operator("cs2.tex_assign", text="Actualizar texturas" if st["assigned"] else "Asignar texturas", icon="FILE_REFRESH")
        _draw_work_bar(lay)
        if st["assigned"]:
            box.operator("cs2.tex_clear", icon="X")
        col = lay.column(align=True)
        col.scale_y = 1.25
        col.operator("cs2.retexture", icon="TEXTURE")
        _draw_work_bar(lay)
        ui_note(lay, "«Rehacer texturas» hace todos los pasos de una vez.")
        lay.operator("cs2.make_textures", icon="TEXTURE")
        if s.studio_adv:
            lay.separator()
            if s.bake_ao:
                box = lay.box()
                box.label(text="Hornear (oclusión real)", icon="LIGHT")
                box.prop(s, "bake_ao_str")
                box.prop(s, "bake_dist")
                box.prop(s, "bake_samples")
                box.prop(s, "bake_margin")
                ui_note(box, "UV únicas: texturas propias por LOD (sin compartir)")
            else:
                box = lay.box()
                box.label(text="Texturas compartidas (settings.json)", icon="LINKED")
                box.prop(s, "tex_share_asset")
                box.label(text="Otro asset: debe ir en el mismo paquete .cok")


class CS2_PT_export(bpy.types.Panel):
    bl_label = "1 · Revisar"
    bl_idname = "CS2_PT_export"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "CS2"

    poll = _tab_poll("EXPORT")

    def draw(self, context):
        s = context.scene.cs2_building
        lay = self.layout
        lay.prop(s, "game", text="Juego")
        cs1 = s.game == "CS1"
        box = lay.box()
        box.label(text="Lista de control", icon="CHECKBOX_HLT")
        for ok, text in (_checklist_cs1(s) if cs1 else _checklist(s)):
            row = box.row()
            row.alert = not ok
            row.label(text=text, icon="CHECKMARK" if ok else "ERROR")
        ui_note(box, "Te avisa si falta algo antes de exportar.")
        if cs1:
            col = lay.column(align=True)
            col.prop(s, "cs1_tris")
            col.prop(s, "cs1_lod_tris")
            row = lay.row(align=True)
            row.scale_y = 1.3
            row.operator("cs2.cs1_generate", icon="MOD_BUILD")
            _draw_work_bar(lay)
            c1 = cs1_objs(s)
            if c1:
                box = lay.box()
                box.label(text="Malla CS1 (ventanas y puertas = imagen)", icon="MOD_DECIM")
                for ob_, lbl in ((c1[0], "Principal"), (c1[1], "LOD")):
                    box.label(text=f"{lbl}: {sum(len(pl.vertices) - 2 for pl in ob_.data.polygons)} triángulos", icon="CHECKMARK")
        else:
            objs = _lod_objs(s)
            if _main_obj(s) is not None and not objs:
                box = lay.box()
                box.label(text="Niveles de detalle (LODs)", icon="MOD_DECIM")
                box.label(text=f"LOD0: {mesh_tris(_main_obj(s))} triángulos. Faltan el LOD1 y el LOD2.", icon="INFO")
                col = box.column()
                col.scale_y = 1.3
                col.operator("cs2.regen_lods", icon="MOD_DECIM")
                _draw_work_bar(box)
            if objs:
                box = lay.box()
                box.label(text="Triángulos (límites del juego)", icon="MOD_DECIM")
                for text, ok in lod_report(objs):
                    row = box.row()
                    row.alert = not ok
                    row.label(text=text, icon="CHECKMARK" if ok else "ERROR")
                box.operator("cs2.regen_lods", icon="MOD_DECIM")
                _draw_work_bar(box)
                bn = budget_note(s, mesh_tris(objs[0]))
                if bn:
                    row = box.row()
                    row.alert = not bn[1]
                    row.label(text=bn[0] + ("" if bn[1] else " · te pasaste (no es un límite duro)"), icon="INFO" if bn[1] else "ERROR")
        if s.studio_adv:
            box = lay.box()
            box.label(text="Limpieza de la malla", icon="BRUSH_DATA")
            box.prop(s, "separate_lods")
            box.prop(s, "clean_on_generate")
            box.prop(s, "clean_on_export")
            box.operator("cs2.clean_mesh", icon="BRUSH_DATA")
            box.operator("cs2.check_holes", icon="VIEWZOOM")
        row = lay.row(align=True)
        row.operator("cs2.copy_report", icon="COPYDOWN", text="Copiar informe" + (f" ({len(_ERRLOG)} errores)" if _ERRLOG else ""))


class CS2_PT_export_go(bpy.types.Panel):
    bl_label = "2 · Exportar"
    bl_idname = "CS2_PT_export_go"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "CS2"

    poll = _tab_poll("EXPORT")

    def draw(self, context):
        s = context.scene.cs2_building
        lay = self.layout
        cs1 = s.game == "CS1"
        lay.prop(s, "export_dir", text="Carpeta")
        if cs1:
            col = lay.column(align=True)
            col.prop(s, "cs1_snow")
            col.prop(s, "cs1_ao")
            if s.cs1_ao:
                col.prop(s, "bake_ao_str")
                col.prop(s, "bake_dist")
            col.prop(s, "cs1_scale")
            ui_note(lay, "Salen: nombre.fbx, nombre_lod.fbx y los mapas _d _n _s _i")
        row = lay.row(align=True)
        row.scale_y = 1.4
        row.operator("cs2.export_cs1" if cs1 else "cs2.export", icon="EXPORT")
        _draw_work_bar(lay)
        _draw_footer(lay, s)


# ----------------------------------------------------------------------------
# Ficha de edificio: JSON (hecho a partir de fotos de referencia) -> edificio
# ----------------------------------------------------------------------------
import difflib

FICHA_SIDES = {"frente": (0, -1), "atras": (0, 1), "izquierda": (-1, 0), "derecha": (1, 0)}
FICHA_BAL_MODE = {"general": "none", "ninguno": "none", "ninguna": "none", "none": "none",
                  "alternados": "alternate", "alternado": "alternate", "alternate": "alternate",
                  "todos": "all", "todas": "all", "all": "all", "azar": "random", "random": "random"}


def _hex_lin(h, default):
    try:
        h = str(h).strip().lstrip("#")
        if len(h) == 3:
            h = "".join(c * 2 for c in h)
        v = [int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4)]
    except Exception:
        return default
    return tuple(c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in v)


def ficha_style_names(kind):
    lib_ensure()
    return [n for n in _LIB["names"] if _LIB["styles"][n].get("kind") == kind]


def _match_style(name, kind, warns, what):
    """Nombre exacto del estilo de la biblioteca (o el más parecido). None = sin estilo / no hallado."""
    if name in (None, "", "ninguno", "NONE", "general", "__GLOBAL__"):
        return None
    names = ficha_style_names(kind)
    if name in names:
        return name
    low = {n.lower(): n for n in names}
    if str(name).lower() in low:
        return low[str(name).lower()]
    close = difflib.get_close_matches(str(name), names, n=1, cutoff=0.55)
    if close:
        warns.append(f"{what}: '{name}' no existe, uso '{close[0]}'")
        return close[0]
    warns.append(f"{what}: '{name}' no existe en la biblioteca (se deja el estilo general)")
    return None


def _num(d, key, lo, hi, default=None):
    v = d.get(key, None)
    if v is None:
        return default
    try:
        return max(lo, min(hi, float(v)))
    except Exception:
        return default


def parse_ficha_text(text):
    t = text.strip()
    if "```" in t:
        parts = t.split("```")
        for part in parts:
            q = part.strip()
            if q.lower().startswith("json"):
                q = q[4:].strip()
            if q.startswith("{"):
                t = q
                break
    a, b = t.find("{"), t.rfind("}")
    if a < 0 or b < 0:
        raise ValueError("No encontré un JSON en el texto")
    return json.loads(t[a:b + 1])


def _apply_wall_overrides(obj, data, warns):
    """Ajustes por pared (frente/atras/izquierda/derecha) escritos en las caras de la forma."""
    if not (data.get("paredes") or data.get("planta_baja")):
        return
    s = bpy.context.scene.cs2_building
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    lays = wall_layers(bm, create=True)
    bm.faces.ensure_lookup_table()
    n_done = 0
    jobs = [(data.get("paredes") or {}, None), (data.get("planta_baja") or {}, s.floor_height * 1.01)]
    for paredes, zlimit in jobs:
      for side, cfg in paredes.items():
          key = str(side).lower()
          key = {"front": "frente", "back": "atras", "atrás": "atras", "left": "izquierda", "right": "derecha"}.get(key, key)
          if key not in FICHA_SIDES or not isinstance(cfg, dict):
              continue
          dx, dy = FICHA_SIDES[key]
          w_style = _match_style(cfg.get("estilo_ventana"), "window", warns, f"{key}: ventana")
          d_style = _match_style(cfg.get("estilo_puerta"), "door", warns, f"{key}: puerta")
          b_style = _match_style(cfg.get("estilo_balcon"), "balcony", warns, f"{key}: balcón")
          count = cfg.get("ventanas_por_piso")
          ww = _num(cfg, "ancho_ventana_m", 0.4, 6.0)
          wh = _num(cfg, "alto_ventana_m", 0.4, 3.0)
          sill = _num(cfg, "antepecho_m", 0.0, 2.0)
          door = str(cfg.get("puerta", "auto")).lower()
          dw_, dh_, dpos = _num(cfg, "puerta_ancho_m", 0.6, 4.0), _num(cfg, "puerta_alto_m", 1.8, 3.5), _num(cfg, "puerta_pos", 0.0, 1.0)
          bal = str(cfg.get("balcones", "general")).lower()
          for f in bm.faces:
              if abs(f.normal.z) > 0.12:
                  continue
              if f.normal.x * dx + f.normal.y * dy < 0.9:
                  continue
              fz = f.calc_center_median().z
              if zlimit is not None and fz > zlimit:
                  continue
              n_done += 1
              fl = f[lays["cs2_flags"]]
              if count is not None or ww is not None or wh is not None or sill is not None:
                  f[lays["cs2_flags"]] = fl | FLAG_WIN
                  f[lays["cs2_w_count"]] = -1 if (count is not None and int(count) == 0) else int(count or 0)
                  f[lays["cs2_w_w"]] = ww if ww is not None else s.win_w
                  f[lays["cs2_w_h"]] = wh if wh is not None else s.win_h
                  f[lays["cs2_w_sill"]] = sill if sill is not None else 0.9
                  f[lays["cs2_w_depth"]] = 0.25
                  f[lays["cs2_w_off"]] = 0.0
              if dw_ is not None or dh_ is not None or dpos is not None:
                  f[lays["cs2_flags"]] = f[lays["cs2_flags"]] | FLAG_DOOR
                  f[lays["cs2_d_w"]] = dw_ if dw_ is not None else 1.2
                  f[lays["cs2_d_h"]] = dh_ if dh_ is not None else min(2.2, s.floor_height - 0.4)
                  f[lays["cs2_d_pos"]] = dpos if dpos is not None else 0.5
                  f[lays["cs2_d_depth"]] = 0.25
              f[lays["cs2_w_style"]] = wall_style_id(w_style or "__GLOBAL__")
              f[lays["cs2_d_style"]] = wall_style_id(d_style or "__GLOBAL__")
              f[lays["cs2_b_style"]] = wall_style_id(b_style or "__GLOBAL__")
              dmode_ = {"auto": 0, "si": 1, "sí": 1, "yes": 1, "no": 2}.get(door, 0)
              if dmode_ == 1 and fz > s.floor_height * 1.01:   # la puerta va solo en planta baja
                  dmode_ = 2
              f[lays["cs2_door_mode"]] = dmode_
              f[lays["cs2_bal_mode"]] = {"general": 0, "ninguno": 1, "ninguna": 1, "todos": 2, "todas": 2,
                                         "alternados": 3, "alternado": 3}.get(bal, 0)
    bm.to_mesh(obj.data)
    bm.free()
    obj.data.update()
    if not n_done:
        warns.append("Ajustes por pared: no encontré paredes para aplicarlos")


_FICHA_STATE = {"dirty": False}


def reset_building_props(s, everything=False):
    """Devuelve todos los parámetros del panel a sus valores por defecto (menos la forma y el modo)."""
    live = s.live_update
    s.live_update = False
    try:
        for prop in s.bl_rna.properties:
            k = prop.identifier
            keep = ("rna_type", "name", "shape_object", "mode", "live_update") if everything else \
                ("rna_type", "name", "shape_object", "mode", "live_update", "uv_pins", "theme", "tex_size", "level", "pbr_library", "pbr_preview", "pbr_prev_size", "tex_share_lods", "tex_share_asset",
                 "asset_category", "zone_type", "export_dir", "edit_scope", "show_grid")
            if k in keep:
                continue
            try:
                s.property_unset(k)
            except Exception:
                _log_exc("reset_building_props")
    finally:
        s.live_update = live


def apply_ficha(context, data, warns):
    s = context.scene.cs2_building
    reset_building_props(s)   # arranca de valores de fábrica: lo que no diga la ficha no hereda de antes
    ta = data.get("tipo_asset")
    if isinstance(ta, dict):
        cat = {"zonificado": "ZONED", "zoned": "ZONED", "servicio": "SERVICE", "service": "SERVICE",
               "emblematico": "SIGNATURE", "emblemático": "SIGNATURE", "signature": "SIGNATURE"}.get(str(ta.get("categoria", "")).lower())
        if cat:
            s.asset_category = cat
        zn = {"OTRO": "OTHER"}.get(str(ta.get("zona", "")).upper(), str(ta.get("zona", "")).upper())
        if zn in ZONE_TYPES:
            s.zone_type = zn
        elif zn:
            warns.append(f"Tipo de zona '{zn}' no existe")
    live = s.live_update
    s.live_update = False
    try:
        if data.get("nombre"):
            s.building_name = str(data["nombre"])[:40]
        pl = data.get("planta") or {}
        kind = str(pl.get("tipo", "BOX")).upper()
        kind = {"CAJA": "BOX", "RECTANGULAR": "BOX"}.get(kind, kind)
        if kind not in ("BOX", "L", "T", "U"):
            warns.append(f"Planta '{kind}' no soportada: uso caja")
            kind = "BOX"
        s.custom_size = True
        s.width = _num(pl, "ancho_m", 4.0, 120.0, s.width)
        s.depth = _num(pl, "fondo_m", 4.0, 120.0, s.depth)
        s.floors = int(_num(data, "pisos", 1, 40, s.floors))
        s.floor_height = _num(data, "altura_piso_m", 2.4, 5.0, s.floor_height)
        v = data.get("ventanas") or {}
        s.win_w = _num(v, "ancho_m", 0.6, 3.0, s.win_w)
        s.win_h = _num(v, "alto_m", 0.6, 2.2, s.win_h)
        for key, kd, prop in (("ventanas", "window", "window_style"), ("puertas", "door", "door_style"),
                              ("balcones", "balcony", "balcony_style")):
            name = _match_style((data.get(key) or {}).get("estilo"), kd, warns, key)
            try:
                setattr(s, prop, name if name else "NONE")
            except Exception:
                _log_exc("apply_ficha")
        b = data.get("balcones") or {}
        mode = FICHA_BAL_MODE.get(str(b.get("modo", "")).lower())
        if mode:
            s.balconies = mode
        bw = str(b.get("paredes", "")).lower()
        if bw in ("frente", "front"):
            s.balcony_walls = "front"
        elif bw in ("todas", "all"):
            s.balcony_walls = "all"
        col = data.get("colores") or {}
        if col.get("fachada"):
            s.facade_color = _hex_lin(col["fachada"], tuple(s.facade_color))
        if col.get("molduras"):
            s.trim_color = _hex_lin(col["molduras"], tuple(s.trim_color))
        if col.get("techo"):
            s.roof_color = _hex_lin(col["techo"], tuple(s.roof_color))
        if col.get("puertas"):
            s.door_color = _hex_lin(col["puertas"], tuple(s.door_color))
        if col.get("porton_garaje"):
            s.gar_color = _hex_lin(col["porton_garaje"], tuple(s.gar_color))
        if col.get("barandas"):
            s.rail_color = _hex_lin(col["barandas"], tuple(s.rail_color))
        for key, prop in (("material_b", "mat_b_color"), ("material_c", "mat_c_color"), ("material_d", "mat_d_color")):
            if col.get(key):
                setattr(s, prop, _hex_lin(col[key], tuple(getattr(s, prop))))
        pc = data.get("porche")
        if isinstance(pc, dict):
            pkind = str(pc.get("modelo", "")).upper()
            if pkind in PORCH_PRESETS:
                vals = dict(PORCH_DEF)
                vals.update(walls=1)
                vals.update(PORCH_PRESETS[pkind][1])
                vals["d"] = _num(pc, "profundidad_m", 0.8, 6.0, vals["d"])
                if "ancho_m" in pc:
                    vals["w"] = _num(pc, "ancho_m", 0.0, 40.0, vals["w"])
                if "tamano" in pc:
                    vals["sz"] = _num(pc, "tamano", 0.4, 3.0, 1.0)
                dn = str(pc.get("paredes", "")).lower()
                if dn in ("costados", "frente y costados"):
                    vals["walls"] = 2
                elif dn in ("todas", "all"):
                    vals["walls"] = 3
                s.por_on_g = True
                s.por_walls_g = str(vals["walls"])
                for k in PORCH_ENUMS:
                    setattr(s, "por_" + k + "_g", str(int(vals[k])))
                for k in PORCH_NUMS:
                    setattr(s, "por_" + k + "_g", float(vals[k]))
            elif pkind:
                warns.append(f"Porche: modelo '{pkind}' no existe")
        rv = data.get("revestimiento")
        if isinstance(rv, dict):
            mats = {"molduras": "1", "a": "2", "fachada": "2", "b": "3", "c": "4", "d": "5"}
            gm = {"a": "1", "fachada": "1", "b": "2", "c": "3", "d": "4"}
            if str(rv.get("paredes_material", "")).lower() in gm:
                s.wall_mat_g = gm[str(rv["paredes_material"]).lower()]
            fm_ = {"molduras": "1", "a": "2", "b": "3", "c": "4", "d": "5"}
            if str(rv.get("marcos_ventana", "")).lower() in fm_:
                s.frm_mat_g = fm_[str(rv["marcos_ventana"]).lower()]
            pn = rv.get("panos")
            if isinstance(pn, dict):
                md = {"ninguno": "1", "azar": "2", "ajedrez": "3", "columnas": "4", "todas": "5"}.get(str(pn.get("modo", "azar")).lower())
                if md:
                    s.pan_mode_g = md
                pm = {"b": "1", "c": "2", "d": "3", "bc": "4", "bcd": "5"}.get(str(pn.get("material", "b")).lower().replace("+", "").replace(" ", ""))
                if pm:
                    s.pan_mat_g = pm
                s.pan_pad_g = _num(pn, "margen_m", 0.0, 1.5, s.pan_pad_g)
                s.pan_prob_g = _num(pn, "proporcion", 0.0, 1.0, s.pan_prob_g)
            mc_ = rv.get("marcos")
            if isinstance(mc_, dict):
                s.cas_w_g = _num(mc_, "ancho_m", 0.0, 0.8, s.cas_w_g)
                s.cas_d_g = _num(mc_, "saliente_m", 0.01, 0.5, s.cas_d_g)
                s.cas_mat_g = mats.get(str(mc_.get("material", "molduras")).lower(), s.cas_mat_g)
            lm = rv.get("lamas")
            if isinstance(lm, dict):
                s.slt_mode_g = {"ninguna": "0", "vertical": "1", "horizontal": "2"}.get(str(lm.get("tipo", "vertical")).lower(), "0")
                s.slt_gap_g = _num(lm, "separacion_m", 0.1, 1.5, s.slt_gap_g)
                s.slt_w_g = _num(lm, "grosor_m", 0.02, 0.4, s.slt_w_g)
                s.slt_d_g = _num(lm, "saliente_m", 0.02, 0.4, s.slt_d_g)
                s.slt_mat_g = mats.get(str(lm.get("material", "d")).lower(), s.slt_mat_g)
            mw_ = rv.get("muro_cortina")
            if isinstance(mw_, dict):
                s.cw_on_g = bool(mw_.get("activo", True))
                s.cw_mw_g = _num(mw_, "modulo_ancho_m", 0.4, 6.0, s.cw_mw_g)
                s.cw_mh_g = _num(mw_, "modulo_alto_m", 0.0, 6.0, s.cw_mh_g)
                s.cw_pt_g = _num(mw_, "perfil_grosor_m", 0.0, 0.4, s.cw_pt_g)
                s.cw_pd_g = _num(mw_, "perfil_saliente_m", 0.02, 0.6, s.cw_pd_g)
                s.cw_band_g = _num(mw_, "banda_opaca_m", 0.0, 1.5, s.cw_band_g)
                s.cw_edge_g = _num(mw_, "margen_lateral_m", 0.0, 8.0, s.cw_edge_g)
                s.cw_finn_g = int(_num(mw_, "aleta_cada", 0, 12, s.cw_finn_g))
                s.cw_find_g = _num(mw_, "aleta_saliente_m", 0.05, 1.5, s.cw_find_g)
                s.cw_fint_g = _num(mw_, "aleta_grosor_m", 0.03, 0.6, s.cw_fint_g)
                s.cw_bmat_g = mats.get(str(mw_.get("material_banda", "c")).lower(), s.cw_bmat_g)
                s.cw_mmat_g = mats.get(str(mw_.get("material_perfiles", "c")).lower(), s.cw_mmat_g)
                s.cw_fmat_g = mats.get(str(mw_.get("material_aletas", "molduras")).lower(), s.cw_fmat_g)
        c = data.get("cornisa")
        if isinstance(c, dict):
            s.string_courses = bool(c.get("activa", True))
            st = str(c.get("estilo", "")).upper()
            if st in ("BAND", "DOUBLE", "STEPPED", "CORNICE"):
                s.string_style = st
            s.string_h = _num(c, "alto_m", 0.04, 0.6, s.string_h)
            s.string_out = _num(c, "saliente_m", 0.0, 0.3, s.string_out)
            s.string_every = int(_num(c, "cada_pisos", 1, 6, s.string_every))
        elif c is False:
            s.string_courses = False
        t = data.get("techo") or {}
        mod = t.get("modelo")
        if mod in ROOF_PRESETS:
            s.r_preset = mod
            _roof_preset_apply(s, context)
        elif mod:
            warns.append(f"Techo: modelo '{mod}' no existe")
        tp = str(t.get("tipo", "")).upper()
        if tp in ROOF_TYPE_ID:
            s.r_type = tp
        s.r_pitch = _num(t, "inclinacion_grados", 3, 75, s.r_pitch)
        s.r_peak = _num(t, "cumbrera_descentrada", -0.8, 0.8, s.r_peak)
        dmk = {"ninguna": "0", "una_agua": "1", "dos_aguas": "2", "cuatro_aguas": "3", "arqueada": "4"}.get(str(t.get("buhardillas", "")).lower())
        if dmk:
            s.r_dm = dmk
            s.r_dm_n = int(_num(t, "buhardillas_cantidad", 1, 8, s.r_dm_n))
        s.r_pitch2 = _num(t, "inclinacion_tapa_grados", 3, 60, s.r_pitch2)
        s.r_oe = _num(t, "alero_m", 0, 2.5, s.r_oe)
        s.r_ov = _num(t, "alero_hastial_m", 0, 2.5, s.r_ov)
        for _k, _p in (("alero_frente_m", "r_sd_fr"), ("alero_fondo_m", "r_sd_bk"),
                       ("alero_izquierda_m", "r_sd_lf"), ("alero_derecha_m", "r_sd_rg")):
            if _k in t:
                setattr(s, _p, _num(t, _k, 0, 3.0, -1.0))
        if str(t.get("material_cumbrera", "")).lower() in ("techo", "molduras"):
            s.r_capm = "1" if str(t["material_cumbrera"]).lower() == "techo" else "0"
        s.r_t = _num(t, "espesor_m", 0, 0.6, s.r_t)
        if t.get("caballete") in ("largo", "corto"):
            s.r_ridge = "0" if t["caballete"] == "largo" else "1"
        if "tejas" in t:
            s.r_ribs = bool(t["tejas"])
        if "chimeneas" in t:
            s.r_chn = int(_num(t, "chimeneas", 0, 2, 0))
    finally:
        s.live_update = live
    # forma nueva con la planta pedida
    old = s.shape_object
    res = bpy.ops.cs2.create_shape(kind=kind, keep=True)
    _FICHA_STATE["dirty"] = True
    if "FINISHED" not in res:
        raise RuntimeError("No se pudo crear la forma")
    if old is not None and old != s.shape_object:
        try:
            old.hide_viewport = True
        except Exception:
            _log_exc("apply_ficha")
    _apply_wall_overrides(s.shape_object, data, warns)
    regenerate(context)
    ignorados = data.get("no_soportado") or data.get("elementos_no_soportados")
    if ignorados:
        warns.append("Fuera del alcance del add-on (lo agregás a mano): " + ", ".join(map(str, ignorados))[:200])


def _tpl(cat, zona, nombre, ancho, fondo, pisos, alt, techo, colores, ventanas=None, **extra):
    d = {"tipo_asset": {"categoria": cat, "zona": zona}, "nombre": nombre,
         "planta": {"tipo": "BOX", "ancho_m": ancho, "fondo_m": fondo}, "pisos": pisos, "altura_piso_m": alt,
         "colores": colores, "techo": techo}
    if ventanas:
        d["ventanas"] = ventanas
    d.update(extra)
    return d


def _walls(f, a, l, r, **fr):
    return {"frente": dict({"ventanas_por_piso": f}, **fr), "atras": {"ventanas_por_piso": a, "puerta": "no"},
            "izquierda": {"ventanas_por_piso": l, "puerta": "no"}, "derecha": {"ventanas_por_piso": r, "puerta": "no"}}


_SHOP = {"ventanas_por_piso": 3, "ancho_ventana_m": 3.6, "alto_ventana_m": 2.6, "antepecho_m": 0.15, "balcones": "ninguno", "puerta": "si"}
TYPE_TEMPLATES = {
    "RESLOW": ("Casa (residencial baja densidad)", _tpl("zonificado", "RESLOW", "ResidentialLow01", 12, 9, 2, 2.9,
        {"modelo": "GABLE_TILE", "inclinacion_grados": 30, "alero_m": 0.5},
        {"fachada": "#e8e0cf", "molduras": "#d9d2c3", "techo": "#8a4a2b", "puertas": "#4a3020"},
        {"ancho_m": 1.1, "alto_m": 1.4}, paredes=_walls(3, 2, 2, 2, puerta="si"))),
    "RESROW": ("Casas en hilera (media densidad)", _tpl("zonificado", "RESROW", "ResidentialLowRow01", 16, 10, 3, 2.9,
        {"modelo": "GABLE_TILE", "inclinacion_grados": 28, "alero_m": 0.4},
        {"fachada": "#b8734f", "molduras": "#e8e0cf", "techo": "#4a4a4d", "puertas": "#2d3a4a"},
        {"ancho_m": 1.1, "alto_m": 1.5}, paredes=_walls(4, 2, 1, 1, puerta="si"))),
    "RESMED": ("Edificio de departamentos (media densidad)", _tpl("zonificado", "RESMED", "ResidentialHighMed01", 24, 16, 5, 3.0,
        {"modelo": "FLAT"}, {"fachada": "#d8d2c4", "molduras": "#b8b2a4", "techo": "#555555", "puertas": "#3a3a3d"},
        {"ancho_m": 1.3, "alto_m": 1.5}, balcones={"modo": "alternados", "paredes": "frente"}, paredes=_walls(5, 4, 3, 3, puerta="si"))),
    "RESHIGH": ("Torre de departamentos (alta densidad)", _tpl("zonificado", "RESHIGH", "ResidentialHigh01", 32, 24, 12, 3.0,
        {"modelo": "FLAT"}, {"fachada": "#cfd3d6", "molduras": "#9aa0a5", "techo": "#555555", "puertas": "#3a3a3d"},
        {"ancho_m": 1.4, "alto_m": 1.6}, balcones={"modo": "todos", "paredes": "frente"}, paredes=_walls(7, 6, 5, 5, puerta="si"))),
    "RESMIX": ("Mixto: locales abajo y viviendas arriba", _tpl("zonificado", "RESMIX", "ResidentialHighMixed01", 24, 16, 4, 3.2,
        {"modelo": "FLAT"}, {"fachada": "#c9b79c", "molduras": "#e8e0cf", "techo": "#555555", "puertas": "#2d3a4a"},
        {"ancho_m": 1.3, "alto_m": 1.5}, balcones={"modo": "alternados", "paredes": "frente"},
        paredes=_walls(5, 4, 3, 3, puerta="no"), planta_baja={"frente": dict(_SHOP)})),
    "RESRENT": ("Vivienda de alquiler bajo", _tpl("zonificado", "RESRENT", "ResidentialLowRent01", 20, 12, 4, 2.8,
        {"modelo": "FLAT"}, {"fachada": "#a89a86", "molduras": "#8a7e6e", "techo": "#555555", "puertas": "#3a3a3d"},
        {"ancho_m": 1.0, "alto_m": 1.3}, paredes=_walls(5, 4, 3, 3, puerta="si"))),
    "COMLOW": ("Local comercial (baja densidad)", _tpl("zonificado", "COMLOW", "CommercialLow01", 16, 12, 1, 4.2,
        {"modelo": "FLAT"}, {"fachada": "#d9d2c3", "molduras": "#8a8a85", "techo": "#555555", "puertas": "#2d3a4a"},
        {"ancho_m": 1.5, "alto_m": 1.4}, paredes={"frente": dict(_SHOP), "atras": {"ventanas_por_piso": 1, "puerta": "no"},
                                                   "izquierda": {"ventanas_por_piso": 1, "puerta": "no"}, "derecha": {"ventanas_por_piso": 1, "puerta": "no"}})),
    "COMHIGH": ("Edificio comercial (alta densidad)", _tpl("zonificado", "COMHIGH", "CommercialHigh01", 24, 16, 4, 3.6,
        {"modelo": "FLAT_SLAB", "alero_m": 0.3}, {"fachada": "#c5c9cc", "molduras": "#8a8f93", "techo": "#555555", "puertas": "#2d3a4a"},
        {"ancho_m": 2.2, "alto_m": 1.9}, paredes=_walls(5, 4, 3, 3, puerta="no"), planta_baja={"frente": dict(_SHOP, ventanas_por_piso=4)})),
    "OFFLOW": ("Oficinas (baja densidad)", _tpl("zonificado", "OFFLOW", "Office01", 24, 16, 3, 3.4,
        {"modelo": "FLAT_SLAB", "alero_m": 0.3}, {"fachada": "#d4d8dc", "molduras": "#7d858c", "techo": "#555555", "puertas": "#2d3a4a"},
        {"ancho_m": 2.4, "alto_m": 1.8}, paredes=_walls(6, 5, 3, 3, puerta="si"))),
    "OFFHIGH": ("Torre de oficinas (alta densidad)", _tpl("zonificado", "OFFHIGH", "OfficeHigh01", 32, 24, 14, 3.6,
        {"modelo": "FLAT"}, {"fachada": "#b9c4cc", "molduras": "#5d6b75", "techo": "#555555", "puertas": "#2d3a4a"},
        {"ancho_m": 2.8, "alto_m": 2.4}, paredes=_walls(8, 8, 6, 6, puerta="si"))),
    "IND": ("Fábrica (industrial manufactura)", _tpl("zonificado", "IND", "IndustrialManufacturing01", 24, 16, 1, 7.0,
        {"modelo": "GABLE_METAL", "inclinacion_grados": 12, "alero_m": 0.4, "chimeneas": 1},
        {"fachada": "#a9a59c", "molduras": "#6d6a63", "techo": "#6f7a82", "puertas": "#3a3a3d", "porton_garaje": "#8a8a85"},
        {"ancho_m": 1.2, "alto_m": 1.0}, puertas={"estilo": "Garaje seccional doble"},
        paredes={"frente": {"ventanas_por_piso": 2, "puerta": "si"}, "atras": {"ventanas_por_piso": 1, "puerta": "no"},
                 "izquierda": {"ventanas_por_piso": 2, "puerta": "no"}, "derecha": {"ventanas_por_piso": 2, "puerta": "no"}})),
    "WARE": ("Depósito / galpón", _tpl("zonificado", "WARE", "IndustrialWarehouse01", 40, 24, 1, 8.0,
        {"modelo": "GABLE_METAL", "inclinacion_grados": 8, "alero_m": 0.4},
        {"fachada": "#b8b4aa", "molduras": "#7a776f", "techo": "#7f8a92", "puertas": "#3a3a3d", "porton_garaje": "#8a8a85"},
        {"ancho_m": 1.2, "alto_m": 0.9}, puertas={"estilo": "Garaje enrollable doble"},
        paredes={"frente": {"ventanas_por_piso": 3, "puerta": "si"}, "atras": {"ventanas_por_piso": 2, "puerta": "no"},
                 "izquierda": {"ventanas_por_piso": 2, "puerta": "no"}, "derecha": {"ventanas_por_piso": 2, "puerta": "no"}})),
    "SERVICE": ("Servicio: estación (bomberos, policía…)", _tpl("servicio", "OTHER", "ServiceStation01", 24, 16, 2, 4.0,
        {"modelo": "HIP_TILE", "inclinacion_grados": 22, "alero_m": 0.5},
        {"fachada": "#b5483c", "molduras": "#e8e0cf", "techo": "#4a4a4d", "puertas": "#2d3a4a", "porton_garaje": "#d8d2c4"},
        {"ancho_m": 1.3, "alto_m": 1.5}, puertas={"estilo": "Garaje seccional doble"},
        paredes=_walls(4, 3, 3, 3, puerta="si"))),
    "SIGNATURE": ("Emblemático: torre singular", _tpl("emblematico", "OTHER", "SignatureTower01", 24, 24, 30, 3.8,
        {"modelo": "FLAT_SLAB", "alero_m": 0.4}, {"fachada": "#c8ccd0", "molduras": "#7d858c", "techo": "#555555", "puertas": "#2d3a4a"},
        {"ancho_m": 2.6, "alto_m": 2.4}, paredes=_walls(6, 6, 6, 6, puerta="si"))),
}


class CS2_OT_type_template(bpy.types.Operator):
    bl_idname = "cs2.type_template"
    bl_label = "Empezar con una plantilla"
    bl_description = "Crea un edificio de arranque del tipo elegido (medidas, techo y colores típicos) para que lo ajustes. Reemplaza la forma actual"
    bl_options = {"REGISTER", "UNDO"}

    kind: EnumProperty(name="Plantilla", items=[(k, v[0], "") for k, v in TYPE_TEMPLATES.items()])

    def invoke(self, context, event):
        if context.scene.cs2_building.shape_object is not None:
            return context.window_manager.invoke_confirm(self, event, message="Se crea un edificio nuevo (la forma actual queda oculta)")
        return self.execute(context)

    def execute(self, context):
        import copy
        data = copy.deepcopy(TYPE_TEMPLATES[self.kind][1])
        return _run_ficha(self, context, data)


def build_ficha_prompt():
    lib_ensure()
    win = "\n".join("- " + n for n in ficha_style_names("window"))
    door = "\n".join("- " + n for n in ficha_style_names("door"))
    bal = "\n".join("- " + n for n in ficha_style_names("balcony"))
    roofs = "\n".join(f"- {k}: {v[0]}" for k, v in ROOF_PRESETS.items())
    return FICHA_PROMPT.replace("{{VENTANAS}}", win).replace("{{PUERTAS}}", door) \
        .replace("{{BALCONES}}", bal).replace("{{TECHOS}}", roofs)


FICHA_PROMPT = """Sos un arquitecto que prepara la "ficha" de un edificio (residencial, comercial, de oficinas, industrial, de servicios o emblemático) para un add-on de Blender que genera edificios para Cities: Skylines 2. Te paso fotos de referencia del edificio. Tu trabajo es mirarlas con atención y devolver UNA ficha en JSON con la mayor fidelidad posible.

# Cómo trabajar
1. Mirá todas las fotos y decidí qué lado es el "frente" (el de la entrada principal / el más fotografiado). Izquierda y derecha se cuentan mirando el edificio DESDE el frente. Si hay un lado que no se ve en ninguna foto, suponelo igual al lado opuesto y anotalo en "notas".
2. Estimá las medidas en metros usando referencias de escala: una puerta mide ~2,1 m de alto; una ventana de vivienda ~1,1-1,4 m de ancho; la altura de un piso ronda 2,6-3,2 m; un auto mide ~4,5 m de largo. Redondeá a 0,5 m. Si te dije el ancho o los pisos reales, usá mis datos y no los tuyos.
3. Contá pisos (la planta baja cuenta como piso). Si hay buhardillas o mansarda habitable, no las sumes como piso: describilas en el techo.
4. Contá las ventanas POR PISO en cada fachada visible (promedio; si la planta baja difiere, usá el valor del piso típico).
5. Elegí los estilos SOLO de las listas de abajo, copiando el nombre EXACTO. Si ninguno se parece, usá null.
6. Elegí el techo: primero buscá el "modelo" más parecido de la lista y después corregí los números (inclinación, alero) para que coincidan con la foto.
7. Colores: sacalos de la foto como hex (#rrggbb) del color real del material, no de las sombras.
8. Todo lo que el add-on no puede hacer (escaleras exteriores, garaje en subsuelo, plantas con curvas, pérgolas sueltas, etc.) va en "no_soportado" como texto corto. Porches, buhardillas, chimeneas y portones de garaje SÍ se pueden hacer: usá las claves "porche", "techo.buhardillas", "techo.chimeneas" y un estilo de puerta que empiece con Garaje.
9. Si algo no se ve o no estás seguro, poné tu mejor estimación y explicalo en "notas". No inventes detalles que no se ven.

# Formato de salida
Respondé SOLO con un bloque de código ```json con esta estructura (sacá las claves que no apliquen; no agregues comentarios dentro del JSON):

```json
{
  "nombre": "ResidentialLow_CasaEsquina",
  "tipo_asset": {"categoria": "zonificado | servicio | emblematico", "zona": "RESLOW | RESROW | RESMED | RESHIGH | RESMIX | RESRENT | COMLOW | COMHIGH | OFFLOW | OFFHIGH | IND | WARE | OTRO"},
  "planta": {"tipo": "BOX", "ancho_m": 12.0, "fondo_m": 9.0},
  "pisos": 2,
  "altura_piso_m": 2.9,
  "colores": {"fachada": "#e8e0cf", "molduras": "#d9d2c3", "techo": "#8a4a2b", "puertas": "#4a3020", "porton_garaje": "#8a8a85", "barandas": "#2a2a2a",
             "material_b": "#8b3a2b", "material_c": "#3a3a3d", "material_d": "#9a6a3a"},
  "porche": {"modelo": "NINGUNO o FARMER | WRAP | GABLED | PORTICO | SHEDP | HIPP | VERANDA | SCREENED | ENCLOSED | SLEEPING | DECK", "profundidad_m": 1.6, "ancho_m": 3.2, "tamano": 1.0, "paredes": "frente | frente y costados | todas"},
  "revestimiento": {"paredes_material": "A | B | C | D", "marcos_ventana": "molduras | C",
                    "panos": {"modo": "ninguno | azar | ajedrez | columnas | todas", "material": "B | C | D | BC | BCD", "margen_m": 0.35, "proporcion": 0.5},
                    "marcos": {"ancho_m": 0.0, "saliente_m": 0.08, "material": "molduras | A | B | C | D"},
                    "lamas": {"tipo": "ninguna | vertical | horizontal", "separacion_m": 0.3, "grosor_m": 0.07, "saliente_m": 0.06, "material": "D"},
                    "muro_cortina": {"activo": false, "modulo_ancho_m": 1.5, "modulo_alto_m": 0, "perfil_grosor_m": 0.06, "perfil_saliente_m": 0.08, "banda_opaca_m": 0.45, "margen_lateral_m": 0.0, "aleta_cada": 0, "aleta_saliente_m": 0.35, "aleta_grosor_m": 0.12, "material_banda": "C", "material_perfiles": "C", "material_aletas": "molduras"}},
  "ventanas": {"ancho_m": 1.1, "alto_m": 1.5, "estilo": "NOMBRE EXACTO O null"},
  "puertas": {"estilo": "NOMBRE EXACTO O null (si hay portón de garaje visible usá un estilo que empiece con Garaje; la medida del portón va sola)"},
  "balcones": {"modo": "ninguno", "paredes": "frente", "estilo": "NOMBRE EXACTO O null"},
  "cornisa": {"activa": true, "estilo": "BAND", "alto_m": 0.15, "saliente_m": 0.05, "cada_pisos": 1},
  "techo": {"modelo": "GABLE_TILE", "inclinacion_grados": 28, "alero_m": 0.5, "alero_hastial_m": 0.35,
            "alero_frente_m": 0.9, "alero_fondo_m": 0.3, "alero_izquierda_m": 0.35, "alero_derecha_m": 0.35,
            "material_cumbrera": "techo | molduras", "buhardillas": "ninguna", "buhardillas_cantidad": 2,
            "caballete": "largo", "tejas": true, "chimeneas": 0},
  "paredes": {
    "frente":    {"ventanas_por_piso": 3, "puerta": "si", "balcones": "general"},
    "atras":     {"ventanas_por_piso": 3, "puerta": "no", "balcones": "ninguno"},
    "izquierda": {"ventanas_por_piso": 2, "puerta": "no"},
    "derecha":   {"ventanas_por_piso": 2, "puerta": "no"}
  },
  "no_soportado": ["porche con columnas"],
  "notas": "Qué supuse y qué no se veía."
}
```

# Tipo de asset (tipo_asset)
Decidí primero qué es el edificio y completá "tipo_asset":
- zonificado (aparece solo al pintar una zona): RESLOW casa; RESROW casas en hilera; RESMED departamentos de media altura; RESHIGH torre de departamentos; RESMIX viviendas con locales abajo; RESRENT vivienda económica; COMLOW local o comercio chico; COMHIGH edificio comercial o shopping; OFFLOW oficinas bajas; OFFHIGH torre de oficinas; IND fábrica; WARE depósito o galpón.
- servicio (lo coloca el jugador: escuela, bomberos, policía, hospital, municipalidad, estación, etc.): zona "OTRO".
- emblematico (monumento o edificio único y famoso, lo coloca el jugador): zona "OTRO".
Poné en "nombre" una palabra clave según el tipo, porque el juego la usa para el interior de las ventanas: ResidentialLow (RESLOW, RESROW, RESRENT), ResidentialHigh (RESMED, RESHIGH, RESMIX), CommercialLow, CommercialHigh, Office (oficinas) o Industrial; ejemplo "CommercialLow_Ferreteria". En servicios y emblemáticos podés usar un nombre propio.
Rasgos típicos: comercial = planta baja con vidrieras grandes (clave "planta_baja"), techo plano o losa y cartel; oficinas = muchas ventanas anchas y pisos altos (3,4 m), torres sin balcones; industrial = pocos pisos de 6 a 9 m de altura, techo GABLE_METAL de poca pendiente, portones de garaje en el frente, chimenea; servicios = portones de garaje (bomberos, ambulancias) y ventanas regulares; emblemático = escala grande y detalle propio: usá el techo y los pisos más parecidos y anotá en "no_soportado" lo que falte (cúpulas, agujas, formas curvas).

# Planta baja distinta (locales comerciales, garajes)
Si la planta baja se ve distinta de los pisos de arriba (vidrieras grandes de comercios, portones), agregá la clave "planta_baja" con el mismo formato que "paredes" (por lado). Esos valores pisan a los de "paredes" solo en el primer piso. Para vidrieras: ventanas_por_piso = cantidad de locales/vidrieras del lado, ancho_ventana_m 3,0-6,0, alto_ventana_m 2,4-3,0, antepecho_m 0-0,3, balcones "ninguno".

# Revestimiento (fachadas modernas)
Los materiales B, C y D son colores lisos extra (típico: B ladrillo rojizo, C gris oscuro, D madera/corten). Si la fachada tiene paños de color en patrón irregular alrededor de las ventanas, completá "revestimiento.panos" (modo azar si es irregular, ajedrez/columnas si es regular). "marcos" son marcos gruesos salientes en las ventanas (ancho_m > 0 para activarlos). "lamas" son listones verticales/horizontales de madera. Para casas modernas: techo.modelo PEAKED_MODERN (dos aguas asimétrico), BUTTERFLY (mariposa) o FLAT_SLAB (losa plana con alero) o SHED_MODERN; revestimiento.paredes_material D si el frente es de listones de madera, y marcos_ventana C (aluminio negro). Ventanas grandes: ventanas.ancho_m hasta 6 y alto_m hasta 3.2. Torres con paredes todas de vidrio (muro cortina): completá "revestimiento.muro_cortina" con activo true; modulo_ancho_m es el ancho de cada paño de vidrio (1.2 a 2.5 típico), modulo_alto_m 0 si hay un paño por piso, banda_opaca_m es la franja llena a la altura de cada losa (0 si el vidrio es continuo), margen_lateral_m deja paños llenos (material_banda) a los costados de cada pared, y aleta_cada pone aletas verticales claras cada N paños (0 = sin aletas). El muro cortina reemplaza ventanas, puertas y balcones. Porches (galerías cubiertas en la entrada): usá "porche" con el modelo más parecido: FARMER (techo de una agua sobre todo el frente, sin baranda), WRAP (envolvente: frente y costados), GABLED (con frontón sobre la puerta), PORTICO (pórtico chico con frontón), SHEDP (una agua, ancho parcial), HIPP (cuatro aguas), VERANDA (envolvente con baranda), SCREENED (con mosquitero), ENCLOSED (cerrado vidriado), SLEEPING (dos niveles), DECK (terraza sin techo). Buhardillas (ventanas en el techo): techo.buhardillas = ninguna | una_agua | dos_aguas | cuatro_aguas | arqueada, techo.buhardillas_cantidad (1 a 4 por lado) y solo con techos DOS AGUAS, CUATRO AGUAS o una agua. Si nada de esto se ve, omití "revestimiento" y los colores material_*.

# Balcones embutidos (loggias)
Si los balcones NO sobresalen de la fachada (están metidos hacia adentro con baranda al ras), usá un estilo de balcón que empiece con "Loggia". Si sobresalen, usá los demás.

# Valores permitidos
- tipo_asset.categoria: zonificado | servicio | emblematico. tipo_asset.zona: RESLOW | RESROW | RESMED | RESHIGH | RESMIX | RESRENT | COMLOW | COMHIGH | OFFLOW | OFFHIGH | IND | WARE | OTRO.
- planta.tipo: BOX (rectángulo), L, T, U. Elegí la que mejor aproxime la planta vista en las fotos (el add-on las genera con proporciones fijas; después se editan a mano).
- balcones.modo: ninguno | alternados | todos | azar. balcones.paredes: frente | todas.
- cornisa.estilo: BAND (banda simple), DOUBLE (doble), STEPPED (escalonada), CORNICE (cornisa con vuelo). Si no hay cornisa visible: "activa": false.
- techo.modelo: uno de la lista de techos. techo.tipo (opcional, para forzar): FLAT | SHED | GABLE | HIP | MANSARD | BUTTERFLY (mariposa: dos planos que bajan al centro). techo.cumbrera_descentrada: -0.8 a 0.8 (solo dos aguas; 0 = cumbrera al medio; 0.45 = un lado largo y suave y otro corto y empinado, típico moderno). techo.caballete: largo | corto (corto = la cumbrera corre por el lado corto). techo.inclinacion_grados: 5-60 (techo de teja típico 25-35; chapa 10-18; mansarda faldón 60-75). techo.chimeneas: 0 a 2. techo.alero_frente_m / alero_fondo_m / alero_izquierda_m / alero_derecha_m: alero de cada lado en metros (omitilos si todos los lados tienen el mismo alero; no valen en techos a cuatro aguas ni mansarda). techo.material_cumbrera: techo (la cumbrera del mismo material que las tejas) o molduras (blanca, como el borde). Las limahoyas (encuentros de aguas en plantas L, T o U) se arman solas. tamano del porche: 1.0 es el normal, 0.5 uno chico para una sola puerta, 1.5 uno amplio.
- paredes.<lado>.puerta: auto | si | no. paredes.<lado>.balcones: general | ninguno | todos | alternados. Opcionales por pared: ancho_ventana_m, alto_ventana_m, antepecho_m, puerta_ancho_m, puerta_alto_m, puerta_pos (0 = borde izquierdo de la pared mirándola de frente, 1 = derecho), estilo_ventana, estilo_puerta, estilo_balcon.

# Techos disponibles (techo.modelo)
{{TECHOS}}

# Estilos de ventana disponibles
{{VENTANAS}}

# Estilos de puerta disponibles
{{PUERTAS}}

# Estilos de balcón disponibles
{{BALCONES}}

Ahora mirá las fotos adjuntas y devolveme la ficha.
"""


class CS2_OT_ficha_clip(bpy.types.Operator):
    bl_idname = "cs2.ficha_clip"
    bl_label = "Cargar ficha del portapapeles"
    bl_description = "Crea el edificio a partir de la ficha (JSON) que copiaste. Reemplaza la forma actual"
    bl_options = {"REGISTER", "UNDO"}

    def invoke(self, context, event):
        s = context.scene.cs2_building
        if s.shape_object is not None:
            return context.window_manager.invoke_confirm(self, event, message="Se crea un edificio nuevo (la forma actual queda oculta)")
        return self.execute(context)

    def execute(self, context):
        try:
            data = parse_ficha_text(context.window_manager.clipboard)
        except Exception as e:
            log_error(getattr(self, "bl_idname", "operador"), e)
            self.report({"ERROR"}, f"El portapapeles no tiene una ficha válida: {e}")
            return {"CANCELLED"}
        return _run_ficha(self, context, data)


class CS2_OT_ficha_file(bpy.types.Operator):
    bl_idname = "cs2.ficha_file"
    bl_label = "Cargar ficha desde archivo"
    bl_description = "Crea el edificio a partir de un archivo .json de ficha"
    bl_options = {"REGISTER", "UNDO"}

    filepath: StringProperty(subtype="FILE_PATH")
    filter_glob: StringProperty(default="*.json;*.txt", options={"HIDDEN"})

    def invoke(self, context, event):
        context.window_manager.fileselect_add(self)
        return {"RUNNING_MODAL"}

    def execute(self, context):
        try:
            with open(self.filepath, "r", encoding="utf-8") as fh:
                data = parse_ficha_text(fh.read())
        except Exception as e:
            log_error(getattr(self, "bl_idname", "operador"), e)
            self.report({"ERROR"}, f"No pude leer la ficha: {e}")
            return {"CANCELLED"}
        return _run_ficha(self, context, data)


def _run_ficha(op, context, data):
    warns = []
    try:
        apply_ficha(context, data, warns)
    except Exception as e:
        op.report({"ERROR"}, f"No se pudo armar el edificio: {e}")
        return {"CANCELLED"}
    for w in warns[:8]:
        op.report({"WARNING"}, w)
    notas = data.get("notas")
    op.report({"INFO"}, "Edificio creado desde la ficha" + (f" · Notas: {str(notas)[:120]}" if notas else ""))
    return {"FINISHED"}


class CS2_OT_ficha_prompt(bpy.types.Operator):
    bl_idname = "cs2.ficha_prompt"
    bl_label = "Copiar el prompt para hacer la ficha"
    bl_description = ("Copia al portapapeles el prompt completo (con tu biblioteca de estilos actual). "
                      "Pegalo en un chat con Claude junto con las fotos del edificio")

    def execute(self, context):
        txt = build_ficha_prompt()
        context.window_manager.clipboard = txt
        try:
            lib_ensure()
            path = os.path.join(style_dir(), "PROMPT_ficha.txt")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(txt)
        except Exception:
            _log_exc("execute")
        self.report({"INFO"}, "Prompt copiado: pegalo en Claude con las fotos. También quedó guardado en la carpeta de estilos")
        return {"FINISHED"}


class CS2_PT_ficha(bpy.types.Panel):
    bl_label = "Ficha con IA (desde fotos)"
    bl_idname = "CS2_PT_ficha"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "CS2"

    @classmethod
    def poll(cls, context):
        s = context.scene.cs2_building
        return s.ui_tab == "FORMA" and bool(s.studio_adv)

    def draw(self, context):
        lay = self.layout
        col = lay.column(align=True)
        col.label(text="1. Copiá el prompt y pegalo en Claude")
        col.label(text="    con las fotos del edificio.")
        col.operator("cs2.ficha_prompt", icon="COPYDOWN")
        col.separator()
        col.label(text="2. Copiá el JSON que te devuelva")
        col.label(text="    y creá el edificio:")
        col.operator("cs2.ficha_clip", icon="PASTEDOWN")
        col.operator("cs2.ficha_file", icon="FILE")




def _install_adv_prop():
    CS2BuildingProps.__annotations__["ui_tab"] = EnumProperty(
        name="Sección", default="FORMA", items=[(k, lab, desc, ico, i) for i, (k, lab, desc, ico) in enumerate(UI_TABS)],
        description="Qué paso del add-on se muestra")
    CS2BuildingProps.__annotations__["studio_adv"] = BoolProperty(
        name="Avanzado", default=False,
        description="Muestra las opciones de uso menos frecuente: editor de estilos, muro cortina, horneado, modelos propios y más")


_install_adv_prop()


# ----------------------------------------------------------------------------
# Componentes: un botón y un panel de ajustes aparte por cada elemento personalizable
# (idea de building_tools: cada herramienta abre su propia ventana de ajustes con los valores en vivo).
# Alcance: «Todo el edificio» o «Caras elegidas» (mismo selector de siempre, sin usar el modo edición como medio de trabajo).
# ----------------------------------------------------------------------------
_COMP = {"classes": [], "specs": {}}


def _rna(owner, attr):
    cls = {"CS2BuildingProps": CS2BuildingProps, "CS2WallProps": CS2WallProps, "CS2StyleProps": CS2StyleProps}[owner]
    return cls.bl_rna.properties.get(attr)


def _same_def(a, b):
    """¿Dos propiedades significan lo mismo (tipo, valor inicial, opciones)? Los rangos pueden diferir."""
    if a is None or b is None or a.type != b.type:
        return False
    if a.type == "ENUM":
        return [i.identifier for i in a.enum_items] == [i.identifier for i in b.enum_items] and a.default == b.default
    if a.type in ("FLOAT", "INT"):
        return abs(float(a.default) - float(b.default)) < 1e-9
    return a.default == b.default


def _clone_prop(a, b=None):
    """Propiedad de operador con la definición de `a` (y los rangos más estrechos si `b` es la pareja)."""
    kw = dict(name=a.name, description=a.description)
    if a.type in ("FLOAT", "INT"):
        lo = a.hard_min if b is None else max(a.hard_min, b.hard_min)
        hi = a.hard_max if b is None else min(a.hard_max, b.hard_max)
        slo, shi = max(lo, a.soft_min), min(hi, a.soft_max)
        kw.update(default=a.default, min=lo, max=hi, soft_min=slo, soft_max=shi)
        if a.type == "FLOAT":
            kw.update(precision=getattr(a, "precision", 3), subtype=a.subtype, unit=a.unit)
            return FloatProperty(**kw)
        return IntProperty(**kw)
    if a.type == "BOOLEAN":
        return BoolProperty(default=a.default, **kw)
    if a.type == "ENUM":
        items = [(i.identifier, i.name, i.description) for i in a.enum_items]
        return EnumProperty(items=items, default=a.default, **kw)
    raise TypeError(a.type)


def _F(g=None, w=None, **kw):
    """Campo de un componente: g = propiedad del edificio, w = propiedad de la pared (cara)."""
    return dict(g=g, w=w, **kw)


# --- estilos (ventana / puerta / balcón) dentro del panel ------------------------------------------------------
_ST_SKIP = ("kind",)


def _style_keys():
    return [k for k in STYLE_DEFAULT if k not in _ST_SKIP and k not in CW_STYLE_KEYS]


def _style_groups(kind):
    """Grupos de campos del estilo: (título, [campos], condición)."""
    sc = lambda *n: [("e_" + x) for x in n]
    if kind == "balcony":
        return [
            ("Planta y losa", sc("bal_type", "bal_front", "bal_run", "bal_depth", "bal_extra_w", "bal_slab_t", "bal_support", "bal_bay_h"), None),
            ("Baranda", sc("bal_rail", "bal_rail_h", "bal_bar_gap", "bal_sides", "bal_cap", "bal_cap_w", "bal_cap_h",
                           "bal_bar_r", "bal_post_r", "bal_planter", "bal_planter_h"), None),
            ("Techo del balcón", sc("bal_roof"), None),
        ]
    door = kind == "door"
    g = [("Forma y marco", sc("shape", "arch_rise", "frame_w", "frame_d"), None),
         ("Divisiones", sc("cols", "rows", "bar_w", "bar_d", "transom") + ([] if door else sc("lattice")), None)]
    if door:
        g.append(("Hoja y vidrios", sc("leaves", "leaf_type", "panels", "lite", "lite_n", "glass_frac", "sidelights", "handle",
                                       "garage", "size_w", "size_h"), None))
    g.append(("Moldura y remates", sc("casing_w", "casing_d", "head", "head_h", "pilasters", "pilaster_w")
              + ([] if door else sc("sill", "sill_d", "shutters", "louvers")), None))
    return g


def _style_vis(self, name):
    """Qué campos del estilo se muestran según lo elegido."""
    n = name[2:]
    g = lambda a: getattr(self, "e_" + a)
    if n == "arch_rise":
        return g("shape") in ("ARCH", "POINTED")
    if n == "panels":
        return g("leaf_type") in ("PANELS", "SECTIONAL")
    if n == "lite_n":
        return g("lite") in ("SLOTS", "GRID")
    if n == "glass_frac":
        return g("lite") == "TOP"
    if n == "head_h":
        return g("head") != "NONE"
    if n == "pilaster_w":
        return g("pilasters")
    if n == "sill_d":
        return g("sill") != "NONE"
    if n == "louvers":
        return g("shutters") != "NONE"
    if n == "bal_bay_h":
        return g("bal_type") == "BAY"
    if n in ("bal_rail_h", "bal_sides", "bal_cap", "bal_planter"):
        return g("bal_rail") != "NONE"
    if n == "bal_bar_gap":
        return g("bal_rail") in ("BALUSTERS", "LATTICE", "WROUGHT")
    if n in ("bal_cap_w", "bal_cap_h"):
        return g("bal_rail") != "NONE" and g("bal_cap")
    if n in ("bal_bar_r", "bal_post_r"):
        return g("bal_rail") in ("BALUSTERS", "RODS", "LATTICE", "WROUGHT")
    if n == "bal_planter_h":
        return g("bal_rail") != "NONE" and g("bal_planter")
    return True


def _estilo_items(kind):
    def cb(self, context):
        if getattr(self, "alcance", "ALL") == "FACES":
            return _items(kind, "__GLOBAL__", "Igual al general del edificio", ("__NONE__", "Sin estilo en estas paredes", ""))
        return _items(kind, "NONE", "Sin estilo (abertura simple)" if kind != "balcony" else "Balcón simple (sin estilo)")
    return cb


def _load_style_into(op, st):
    full = {**STYLE_DEFAULT, **(st or {})}
    for k in _style_keys():
        try:
            setattr(op, "e_" + k, full[k])
        except (TypeError, ValueError):
            pass


def _style_from_op(op, kind):
    st = {"kind": kind}
    for k in _style_keys():
        st[k] = getattr(op, "e_" + k)
    return clean_style(st)


def _style_resolve(op, spec):
    """Devuelve el nombre del estilo a usar (o el valor especial) y guarda lo que el usuario ajustó."""
    kind = spec["style"]
    cur = op.estilo
    if cur in ("NONE", "__GLOBAL__", "__NONE__"):
        op.estilo_cargado = cur
        return cur
    if cur != op.estilo_cargado:          # eligió otro estilo: se cargan sus valores en el panel
        _load_style_into(op, get_style(cur))
        op.estilo_cargado = cur
        return cur
    nuevo = op.guardar_como.strip()
    base = get_style(cur)
    mine = _style_from_op(op, kind)
    if nuevo or base is None or clean_style({**STYLE_DEFAULT, **base}) != mine:
        name = nuevo or f"Ajuste de {_KIND_LABEL[kind]}"
        save_user_style(name, mine)
        op.guardar_como = ""
        op.estilo = name
        op.estilo_cargado = name
        queue_thumb(name)
        return name
    return cur


# --- definición de los componentes ---------------------------------------------------------------------------
def _componentes():
    return [
        dict(key="window", label="Ventana", icon="MOD_LATTICE", style="window",
             desc="Ventanas: estilo, tamaño, antepecho, profundidad y cantidad",
             wall_groups=[("win", "use_win", None)],
             groups=[("Tamaño y posición", [_F("win_w", "win_w"), _F("win_h", "win_h"), _F("win_sill", "win_sill"),
                                             _F("win_depth", "win_depth"), _F(None, "win_off")], None),
                     ("Cantidad por piso", [_F(None, "win_auto"), _F(None, "win_count", vis=lambda o: not o.win_auto)], None)]),
        dict(key="door", label="Puerta", icon="MESH_PLANE", style="door",
             desc="Puertas: estilo, medidas y posición",
             wall_groups=[("door", "use_door", None)], wall_modes=[("cs2_door_mode", "door_mode")],
             groups=[("Puerta", [_F(None, "door_mode")], None),
                     ("Tamaño y posición", [_F("door_w_g", None), _F("door_h_g", None), _F(None, "door_w"), _F(None, "door_h"),
                                             _F(None, "door_pos"), _F(None, "door_off"), _F(None, "door_depth")], None)]),
        dict(key="balcony", label="Balcón", icon="SNAP_FACE_CENTER", style="balcony",
             desc="Balcones: estilo, dónde van y medidas",
             wall_groups=[("bal", "use_bal", None)], wall_modes=[("cs2_bal_mode", "bal_mode")],
             groups=[("Dónde", [_F("balconies", None), _F("balcony_prob", None, vis=lambda o: o.balconies == "random"),
                                _F("balcony_walls", None, vis=lambda o: o.balconies != "none"), _F(None, "bal_mode")], None),
                     ("Medidas", [_F("bal_depth_g", "bal_depth", vis=_bal_med), _F("bal_w_g", "bal_w", vis=_bal_med),
                                  _F("bal_rail_g", "bal_rail", vis=_bal_med), _F(None, "bal_off")], None)]),
        dict(key="curtain", label="Muro cortina", icon="MOD_WIREFRAME", cwstyle=True,
             desc="Muro cortina de vidrio: estilos, módulos, perfiles, franjas, aletas y remates",
             wall_groups=[("cw", "use_cw", None)], wall_modes=[("cs2_k_mode", "cw_mode")],
             groups=[("Muro cortina", [_F("cw_on_g", None), _F(None, "cw_mode")], None),
                     ("Módulos", [_F("cw_mw_g", "cw_mw"), _F("cw_mh_g", "cw_mh"), _F("cw_pat_g", "cw_pat"),
                                  _F("cw_patk_g", "cw_patk", vis=lambda o: _val(o, "cw_pat") == "1"),
                                  _F("cw_patn_g", "cw_patn", vis=lambda o: _val(o, "cw_pat") == "2")], None),
                     ("Perfiles", [_F("cw_prof_g", "cw_prof"),
                                   _F("cw_pt_g", "cw_pt", vis=lambda o: _val(o, "cw_prof") != "3"),
                                   _F("cw_pd_g", "cw_pd", vis=lambda o: _val(o, "cw_prof") != "3"),
                                   _F("cw_ht_g", "cw_ht", vis=lambda o: _val(o, "cw_prof") != "3"),
                                   _F("cw_hd_g", "cw_hd", vis=lambda o: _val(o, "cw_prof") != "3"),
                                   _F("cw_mmat_g", "cw_mmat", vis=lambda o: _val(o, "cw_prof") != "3")], None),
                     ("Franjas y bordes", [_F("cw_band_g", "cw_band"), _F("cw_bmat_g", "cw_bmat"), _F("cw_edge_g", "cw_edge")], None),
                     ("Puerta de acceso", [_F("cw_doorw_g", "cw_doorw")], None),
                     ("Zócalo", [_F("cw_baseh_g", "cw_baseh"),
                                 _F("cw_basemat_g", "cw_basemat", vis=lambda o: _val(o, "cw_baseh") > 0)], None),
                     ("Remate superior", [_F("cw_caph_g", "cw_caph"),
                                          _F("cw_capo_g", "cw_capo", vis=lambda o: _val(o, "cw_caph") > 0),
                                          _F("cw_capmat_g", "cw_capmat", vis=lambda o: _val(o, "cw_caph") > 0)], None),
                     ("Esquinas", [_F("cw_cornw_g", "cw_cornw")], None),
                     ("Aletas", [_F("cw_finn_g", "cw_finn"),
                                 _F("cw_find_g", "cw_find", vis=lambda o: _cw_fins(o)),
                                 _F("cw_fint_g", "cw_fint", vis=lambda o: _cw_fins(o)),
                                 _F("cw_fmat_g", "cw_fmat", vis=lambda o: _cw_fins(o))], None)]),
        dict(key="walls", label="Paredes", icon="MATERIAL",
             desc="Acabados de pared: materiales, paños de color, marcos salientes y lamas",
             wall_groups=[("pan", "use_pan", "Paños de color"), ("cas", "use_cas", "Marcos salientes"),
                          ("slt", "use_slt", "Lamas")],
             wall_modes=[("cs2_f_mat", "f_mat"), ("cs2_f_rot", "f_rot")],
             groups=[("Materiales", [_F("wall_mat_g", "f_mat"), _F(None, "f_rot"), _F("ground_mat_g", None),
                                     _F("ground_n_g", None, vis=lambda o: o.ground_mat_g != "0"), _F("frm_mat_g", None)], None),
                     ("Paños de color y crujías",
                      [_F("pan_mode_g", "pan_mode"), _F("pan_mat_g", "pan_mat", vis=lambda o: _pan_on(o)),
                       _F("pan_pad_g", "pan_pad", vis=lambda o: _pan_on(o)),
                       _F("pan_prob_g", "pan_prob", vis=lambda o: _pan_on(o) and _pan_mode(o) in ("2", "9"))], "pan"),
                     ("Marcos salientes en ventanas",
                      [_F("cas_w_g", "cas_w"), _F("cas_d_g", "cas_d", vis=lambda o: _cas_on(o)),
                       _F("cas_mat_g", "cas_mat", vis=lambda o: _cas_on(o))], "cas"),
                     ("Lamas (madera, aluminio)",
                      [_F("slt_mode_g", "slt_mode"), _F("slt_gap_g", "slt_gap", vis=lambda o: _slt_on(o)),
                       _F("slt_w_g", "slt_w", vis=lambda o: _slt_on(o)), _F("slt_d_g", "slt_d", vis=lambda o: _slt_on(o)),
                       _F("slt_mat_g", "slt_mat", vis=lambda o: _slt_on(o))], "slt")]),
        dict(key="grid", label="Retícula", icon="MOD_LATTICE",
             wall_groups=[("gr", "use_gr", None)],
             desc="Retícula estructural: pilastras y losas en relieve con el relleno hundido",
             groups=[("Retícula estructural", [_F("gr_on_g", "gr_on"), _F("gr_walls_g", None, vis=lambda o: o.alcance == "ALL"),
                                               _F("gr_mat_g", "gr_mat", vis=lambda o: _val(o, "gr_on") != "0"),
                                               _F("gr_every_g", "gr_every", vis=lambda o: _val(o, "gr_on") in ("1", "3"))], None),
                     ("Medidas", [_F(f"gr_{k}_g", f"gr_{k}", vis=lambda o: _val(o, "gr_on") != "0") for k in NEW_GROUPS["gr"]["floats"]], None)]),
        dict(key="shop", label="Planta comercial", icon="SHADING_RENDERED",
             wall_groups=[("sh", "use_sh", None)],
             desc="Planta baja comercial: vidriera con pilares, cartel, toldos y acceso hundido",
             groups=[("Planta baja comercial", [_F("sh_on_g", "sh_on"), _F("sh_walls_g", None, vis=lambda o: o.alcance == "ALL")], None),
                     ("Partes", [_F(f"sh_{k}_g", f"sh_{k}", vis=lambda o: _val(o, "sh_on") != "0") for k in ("pmat", "smat", "aw")]
                      + [_F("sh_awmat_g", "sh_awmat", vis=lambda o: _val(o, "sh_on") != "0" and _val(o, "sh_aw") != "0")], None),
                     ("Medidas", [_F(f"sh_{k}_g", f"sh_{k}", vis=lambda o: _val(o, "sh_on") != "0") for k in NEW_GROUPS["sh"]["floats"]], None)]),
        dict(key="roof", label="Techo", icon="HOME", roof=True,
             desc="Techo: modelo, forma, aleros, cubierta, buhardillas y chimeneas",
             groups=_roof_groups()),
        dict(key="cornice", label="Cornisas", icon="MOD_SOLIDIFY", only_all=True,
             desc="Cornisas entre pisos (valen para todo el edificio)",
             groups=[("Cornisas entre pisos", [_F("string_courses", None),
                                               _F("string_style", None, vis=lambda o: o.string_courses),
                                               _F("string_h", None, vis=lambda o: o.string_courses),
                                               _F("string_out", None, vis=lambda o: o.string_courses),
                                               _F("string_z", None, vis=lambda o: o.string_courses),
                                               _F("string_every", None, vis=lambda o: o.string_courses)], None)]),
        dict(key="porch", label="Porche", icon="ORIENTATION_VIEW", porch=True,
             wall_groups=[("por", "use_por", None)],
             desc="Porches: modelo, techo, baranda, cerramiento, escalones y medidas",
             groups=[("Porche", [_F("por_on_g", "por_on"), _F("por_walls_g", None)], None),
                     ("Partes", [_F("por_roof_g", "por_roof"), _F("por_rail_g", "por_rail"), _F("por_enc_g", "por_enc"),
                                 _F("por_stp_g", "por_stp"), _F("por_lv_g", "por_lv")], None),
                     ("Medidas", [_F("por_%s_g" % k, "por_" + k) for k in PORCH_NUMS], None),
                     ("Materiales", [_F("por_dmat_g", "por_dmat"), _F("por_pmat_g", "por_pmat")], None)]),
    ]


def _bal_med(o):
    return o.alcance == "FACES" or o.estilo in ("NONE", "")


def _cw_fins(o):
    return _val(o, "cw_finn") > 0


def _val(o, base):
    """Valor del campo `base` según el alcance (existe como <base>_g o como <base>)."""
    return getattr(o, base + "_g") if (o.alcance == "ALL" and hasattr(o, base + "_g")) else getattr(o, base)


def _pan_mode(o):
    return _val(o, "pan_mode")


def _pan_on(o):
    return _pan_mode(o) != "1"


def _cas_on(o):
    return _val(o, "cas_w") > 0


def _slt_on(o):
    return _val(o, "slt_mode") != "0"


def _roof_groups():
    fl = lambda o: o.r_type == "FLAT"
    nf = lambda o: o.r_type != "FLAT"
    gsb = lambda o: o.r_type in ("GABLE", "SHED", "BUTTERFLY")
    return [
        ("Modelo", [_F("r_preset", None), _F("r_type", None)], None),
        ("Techo plano (parapet y cornisa)", [_F("r_nopar", None, vis=fl), _F("r_par_h", None, vis=fl), _F("r_par_t", None, vis=fl),
                                              _F("r_nocor", None, vis=fl), _F("r_cor_h", None, vis=fl), _F("r_cor_o", None, vis=fl)], "flat"),
        ("Forma", [_F("r_pitch", None, vis=nf), _F("r_pitch2", None, vis=lambda o: o.r_type == "MANSARD"),
                   _F("r_brk", None, vis=lambda o: o.r_type == "MANSARD"), _F("r_ridge", None, vis=gsb),
                   _F("r_peak", None, vis=lambda o: o.r_type == "GABLE"), _F("r_flip", None, vis=lambda o: o.r_type == "SHED"),
                   _F("r_t", None, vis=nf)], "shape"),
        ("Aleros", [_F("r_oe", None, vis=nf), _F("r_ov", None, vis=gsb), _F("r_sd_fr", None, vis=gsb), _F("r_sd_bk", None, vis=gsb),
                    _F("r_sd_lf", None, vis=gsb), _F("r_sd_rg", None, vis=gsb)], "shape"),
        ("Cubierta", [_F("r_ribs", None, vis=nf), _F("r_rib_gap", None, vis=lambda o: nf(o) and o.r_ribs),
                      _F("r_rib_h", None, vis=lambda o: nf(o) and o.r_ribs), _F("r_cap", None, vis=nf),
                      _F("r_capm", None, vis=lambda o: nf(o) and o.r_cap)], "shape"),
        ("Buhardillas", [_F("r_dm", None, vis=lambda o: o.r_type in ("GABLE", "HIP", "SHED")),
                         _F("r_dm_n", None, vis=lambda o: o.r_type in ("GABLE", "HIP", "SHED") and o.r_dm != "0"),
                         _F("r_dm_side", None, vis=lambda o: o.r_type in ("GABLE", "HIP") and o.r_dm != "0"),
                         _F("r_dm_w", None, vis=lambda o: o.r_type in ("GABLE", "HIP", "SHED") and o.r_dm != "0"),
                         _F("r_dm_h", None, vis=lambda o: o.r_type in ("GABLE", "HIP", "SHED") and o.r_dm != "0"),
                         _F("r_dm_q", None, vis=lambda o: o.r_type in ("GABLE", "HIP", "SHED") and o.r_dm != "0")], "shape"),
        ("Chimeneas", [_F("r_chn", None), _F("r_chpos", None, vis=lambda o: o.r_chn), _F("r_chside", None, vis=lambda o: o.r_chn),
                       _F("r_chw", None, vis=lambda o: o.r_chn), _F("r_chd", None, vis=lambda o: o.r_chn),
                       _F("r_chh", None, vis=lambda o: o.r_chn), _F("r_chcap", None, vis=lambda o: o.r_chn)], None),
    ]


# --- construcción de los operadores ---------------------------------------------------------------------------
def _prep_fields(spec):
    """Resuelve cada campo contra las propiedades reales: nombre del operador, parejas y propiedades clonadas."""
    ann, fields, seen = {}, [], set()
    for title, flist, opt in spec["groups"]:
        for f in flist:
            g, w = f["g"], f["w"]
            ga = _rna("CS2BuildingProps", g) if g else None
            wa = _rna("CS2WallProps", w) if w else None
            if g and ga is None:
                raise KeyError(f"CS2BuildingProps.{g}")
            if w and wa is None:
                raise KeyError(f"CS2WallProps.{w}")
            parts = []
            if ga is not None and wa is not None and _same_def(ga, wa):
                parts.append((w, g, w, _clone_prop(wa, ga)))
            else:
                if ga is not None:
                    parts.append((g, g, None, _clone_prop(ga)))
                if wa is not None:
                    parts.append((w, None, w, _clone_prop(wa)))
            for p, gg, ww, prop in parts:
                if p in seen:
                    raise KeyError("campo repetido " + p)
                seen.add(p)
                ann[p] = prop
                fields.append(dict(p=p, g=gg, w=ww, vis=f.get("vis")))
            f["_p"] = [x[0] for x in parts]
    return ann, fields


def _cw_field_for(op, key):
    """Campo del operador que corresponde a la clave `cw_xxx` del estilo (el del edificio o el de la pared)."""
    for f in op._fields:
        if f["g"] == key + "_g" or f["w"] == key:
            return f["p"]
    return None


def _cw_style_load(op, st):
    props = op.properties.bl_rna.properties
    for k in CW_STYLE_KEYS:
        p = _cw_field_for(op, k)
        if p is None or p not in props:
            continue
        v = st.get(k, STYLE_DEFAULT[k])
        try:
            setattr(op, p, str(int(v)) if props[p].type == "ENUM" else v)
        except (TypeError, ValueError):
            pass


def _cw_style_from_op(op):
    props = op.properties.bl_rna.properties
    st = {"kind": "curtain"}
    for k in CW_STYLE_KEYS:
        p = _cw_field_for(op, k)
        if p is None or p not in props:
            continue
        v = getattr(op, p)
        st[k] = int(v) if props[p].type == "ENUM" else v
    return clean_style(st)


def _cw_style_resolve(op):
    """Elegir un estilo carga sus valores en el panel; «Guardar como» guarda lo que ajustaste como estilo propio."""
    cur = op.estilo
    if cur != "NONE" and cur != op.estilo_cargado:
        st = get_style(cur)
        if st is not None:
            _cw_style_load(op, st)
        op.estilo_cargado = cur
        return
    nuevo = op.guardar_como.strip()
    if nuevo:
        save_user_style(nuevo, _cw_style_from_op(op))
        op.guardar_como = ""
        op.estilo = nuevo
        op.estilo_cargado = nuevo
        queue_thumb(nuevo)
    elif cur == "NONE":
        op.estilo_cargado = "NONE"


def _roof_selected_cfg(context):
    """Techo propio de la primera cara de arriba elegida en Editar forma (dict), o None si no tiene."""
    obj = _edit_shape_obj(context)
    if obj is None:
        return None
    try:
        bm = bmesh.from_edit_mesh(obj.data)
        lays = wall_layers(bm, create=False)
        if not lays:
            return None
        for f in _selected_faces(bm):
            cfg = roof_face_cfg(f, lays) if f.normal.z > 0.12 else None
            if cfg:
                return cfg
    except Exception:
        _log_exc("ajustes_techo_cara")
        return None
    return None


def _roof_load_face(op, context):
    """Con «Caras elegidas»: si la primera cara de arriba elegida ya tiene techo propio, el panel arranca con sus valores
    (así se sigue editando ese techo en vez de empezar de nuevo)."""
    cfg = _roof_selected_cfg(context)
    if not cfg:
        return
    props = op.properties.bl_rna.properties
    for k, v in cfg.items():
        if k == "set" or ("r_" + k) not in props:
            continue
        pr = props["r_" + k]
        try:
            if k == "type":
                setattr(op, "r_type", ROOF_ID_TYPE.get(int(v), "FLAT"))
            elif pr.type == "BOOLEAN":
                setattr(op, "r_" + k, bool(v))
            elif pr.type == "ENUM":
                setattr(op, "r_" + k, str(int(v)))
            elif pr.type == "INT":
                setattr(op, "r_" + k, int(v))
            else:
                setattr(op, "r_" + k, float(v))
        except (TypeError, ValueError):
            pass


def _comp_invoke_state(op, context, spec):
    s = context.scene.cs2_building
    w = context.scene.cs2_wall
    op.alcance = "FACES" if (not spec.get("only_all") and _edit_shape_obj(context) is not None) else "ALL"
    for f in op._fields:
        src, attr = (w, f["w"]) if (f["w"] and op.alcance == "FACES") else (s, f["g"]) if f["g"] else (w, f["w"])
        try:
            setattr(op, f["p"], getattr(src, attr))
        except (TypeError, ValueError):
            pass
    if spec.get("style"):
        kind = spec["style"]
        cur = getattr(s, _KIND_PROP[kind]) if op.alcance == "ALL" else getattr(w, _KIND_WALL[kind])
        try:
            op.estilo = cur
        except (TypeError, ValueError):
            pass
        st = get_style(cur) if cur not in ("NONE", "", "__GLOBAL__", "__NONE__") else None
        _load_style_into(op, st or {"kind": kind})
        op.estilo_cargado = cur
    if spec.get("cwstyle"):
        op.estilo = "NONE"
        op.estilo_cargado = "NONE"
    if spec.get("roof"):
        op.r_preset_cargado = op.r_preset if hasattr(op, "r_preset") else s.r_preset
        if op.alcance == "FACES":
            _roof_load_face(op, context)
    for _gid, use, label in spec.get("wall_groups", []):
        if label:
            try:
                setattr(op, "usar_" + use, True)
            except Exception:
                _log_exc("usar_grupo_pared")


def _comp_apply(op, context, spec):
    s = context.scene.cs2_building
    w = context.scene.cs2_wall
    faces_mode = op.alcance == "FACES" and not spec.get("only_all")
    obj = None
    if faces_mode:
        obj = _edit_shape_obj(context)
        if obj is None or not _selected_faces(bmesh.from_edit_mesh(obj.data)):
            raise RuntimeError("Para «Caras elegidas» entrá en «Editar forma» y elegí una o más caras")
    style_name = _style_resolve(op, spec) if spec.get("style") else None
    if spec.get("cwstyle"):
        _cw_style_resolve(op)
    if spec.get("roof"):
        return _apply_roof(op, context, s, faces_mode, obj)
    if spec.get("porch"):
        _porch_model(op)
    live = s.live_update
    s.live_update = False
    try:
        if not faces_mode:
            for f in op._fields:
                if f["g"]:
                    try:
                        setattr(s, f["g"], getattr(op, f["p"]))
                    except (TypeError, ValueError):
                        pass
            if spec.get("style"):
                try:
                    setattr(s, _KIND_PROP[spec["style"]], style_name)
                except (TypeError, ValueError):
                    pass
        else:
            _WALL_LOCK[0] = True
            try:
                for f in op._fields:
                    if f["w"]:
                        setattr(w, f["w"], getattr(op, f["p"]))
                for _gid, use, label in spec.get("wall_groups", []):
                    setattr(w, use, bool(getattr(op, "usar_" + use)) if label else True)
                if spec.get("style"):
                    setattr(w, _KIND_WALL[spec["style"]], style_name)
            finally:
                _WALL_LOCK[0] = False
            for gid, _use, _label in spec.get("wall_groups", []):
                _wall_update(gid)(w, context)
            for attr, prop in spec.get("wall_modes", []):
                _mode_update(attr)(w, context)
            if spec.get("style"):
                attr = {"window": "cs2_w_style", "door": "cs2_d_style", "balcony": "cs2_b_style"}[spec["style"]]
                _style_attr_update(attr, _KIND_WALL[spec["style"]])(w, context)
    finally:
        s.live_update = live


def _porch_model(op):
    """Si eligió otro modelo de porche, carga sus valores en el panel."""
    if op.modelo == op.modelo_cargado:
        return
    op.modelo_cargado = op.modelo
    data = PORCH_PRESETS.get(op.modelo)
    if not data:
        return
    vals = dict(PORCH_DEF)
    vals.update(walls=1)
    vals.update(data[1])
    pairs = [("on", None)] + [(k, None) for k in PORCH_ENUMS] + [(k, None) for k in PORCH_NUMS]
    for k in PORCH_ENUMS:
        for p in ("por_%s_g" % k, "por_%s" % k):
            if hasattr(op, p):
                try:
                    setattr(op, p, str(int(vals[k])))
                except (TypeError, ValueError):
                    pass
    for k in PORCH_NUMS:
        for p in ("por_%s_g" % k, "por_%s" % k):
            if hasattr(op, p):
                setattr(op, p, float(vals[k]))
    if hasattr(op, "por_walls_g"):
        op.por_walls_g = str(vals["walls"])
    if hasattr(op, "por_on_g"):
        op.por_on_g = True
    if hasattr(op, "por_on"):
        op.por_on = "2"


def _apply_roof(op, context, s, faces_mode, obj):
    if op.r_preset != op.r_preset_cargado:      # cambió el modelo: se cargan sus valores en el panel
        op.r_preset_cargado = op.r_preset
        try:
            _roof_preset_apply(op, context)
        except AttributeError:
            pass          # el final de _roof_preset_apply avisa al edificio; en el operador no hace falta
    if not faces_mode:
        _ROOF_LOCK[0] = True
        try:
            s.r_preset = op.r_preset
            for f in op._fields:
                if f["g"] and f["g"] != "r_preset":
                    try:
                        setattr(s, f["g"], getattr(op, f["p"]))
                    except (TypeError, ValueError):
                        pass
        finally:
            _ROOF_LOCK[0] = False
        return
    bm = bmesh.from_edit_mesh(obj.data)
    lays = wall_layers(bm, create=True)
    faces = [f for f in _selected_faces(bm) if f.normal.z > 0.12]
    if not faces:
        raise RuntimeError("Elegí al menos una cara que mire hacia arriba (el techo)")
    vals = roof_values_ui(op)
    for f in faces:
        if op.volver_general:
            f[lays["cs2_r_set"]] = 0
            continue
        f[lays["cs2_r_set"]] = 1
        f[lays["cs2_r_type"]] = ROOF_TYPE_ID.get(vals["type"], 1)
        for k in ROOF_KEYS_INT + ROOF_KEYS_FLOAT:
            if k in ("set", "type"):
                continue
            f[lays["cs2_r_" + k]] = vals[k]
    bmesh.update_edit_mesh(obj.data, loop_triangles=False, destructive=False)


def _make_component(spec):
    ann, fields = _prep_fields(spec)
    key = spec["key"]
    ann["alcance"] = EnumProperty(name="Aplicar a", default="ALL",
                                  items=[("ALL", "Todo el edificio", "Vale para todo el edificio"),
                                         ("FACES", "Caras elegidas", "Solo las caras elegidas en «Editar forma»")])
    if spec.get("style"):
        ann["estilo"] = EnumProperty(name="Estilo", items=_estilo_items(spec["style"]))
        ann["estilo_cargado"] = StringProperty(options={"HIDDEN"})
        ann["guardar_como"] = StringProperty(name="Guardar como", description="Escribí un nombre para guardar estos ajustes "
                                             "como estilo propio (queda en tu biblioteca, marcado con ★)")
        for k in _style_keys():
            ann["e_" + k] = _clone_prop(_rna("CS2StyleProps", k))
    for _gid, use, label in spec.get("wall_groups", []):
        if label:
            ann["usar_" + use] = BoolProperty(name=label, default=True, description="Aplicar este grupo a las caras elegidas")
    if spec.get("cwstyle"):
        ann["estilo"] = EnumProperty(name="Estilo", items=lambda self, ctx: _items("curtain", "NONE", "Personalizado (mis valores)"))
        ann["estilo_cargado"] = StringProperty(default="NONE", options={"HIDDEN"})
        ann["guardar_como"] = StringProperty(name="Guardar como", description="Escribí un nombre para guardar estos ajustes "
                                             "como estilo propio de muro cortina (queda en tu biblioteca, marcado con ★)")
    if spec.get("roof"):
        ann["r_preset_cargado"] = StringProperty(options={"HIDDEN"})
        ann["volver_general"] = BoolProperty(name="Volver al techo general", default=False,
                                             description="Quita el techo propio de las caras elegidas")
    if spec.get("porch"):
        ann["modelo"] = EnumProperty(name="Modelo", items=[("NONE", "Personalizado", "Tus valores")]
                                     + [(k, v[0], "") for k, v in PORCH_PRESETS.items()])
        ann["modelo_cargado"] = StringProperty(default="NONE", options={"HIDDEN"})

    def invoke(self, context, event):
        _comp_invoke_state(self, context, spec)
        return context.window_manager.invoke_props_dialog(self, width=440, confirm_text="Aplicar")

    def execute(self, context):
        try:
            _comp_apply(self, context, spec)
            regenerate(context, live=True)
        except Exception as e:
            log_error(self.bl_idname, e)
            self.report({"ERROR"}, str(e))
            return {"CANCELLED"}
        return {"FINISHED"}

    def draw(self, context):
        _comp_draw(self, context, spec)

    cls = type(f"CS2_OT_comp_{key}", (bpy.types.Operator,), {
        "bl_idname": f"cs2.comp_{key}", "bl_label": spec["label"], "bl_description": spec["desc"],
        "bl_options": {"UNDO"}, "__annotations__": ann,
        "_fields": fields, "invoke": invoke, "execute": execute, "draw": draw,
        "poll": classmethod(lambda cls, context: getattr(context.scene, "cs2_building", None) is not None),
    })
    return cls


def _comp_draw(op, context, spec):
    lay = op.layout
    s = context.scene.cs2_building
    faces = op.alcance == "FACES" and not spec.get("only_all")
    if not faces:
        lay.label(text="Vale para todo el edificio", icon="HOME")
    else:
        editing, n = _faces_state(context)
        if not editing:
            lay.label(text="Entrá en «Editar forma» y elegí caras", icon="ERROR")
        elif n == 0:
            lay.label(text="Hacé clic en una o más caras de la forma", icon="ERROR")
        else:
            lay.label(text=f"Solo en {n} cara(s) elegida(s)", icon="SNAP_FACE")
    if spec.get("cwstyle"):
        box = lay.box()
        box.label(text="Estilo", icon="MODIFIER")
        box.template_icon_view(op, "estilo", show_labels=True, scale=7.0, scale_popup=9.0)
        col = box.column(align=True)
        col.use_property_split = True
        col.use_property_decorate = False
        col.prop(op, "guardar_como")
    if spec.get("porch"):
        col = lay.column(align=True)
        col.use_property_split = True
        col.use_property_decorate = False
        col.prop(op, "modelo")
    if spec.get("style"):
        box = lay.box()
        box.label(text="Estilo", icon="MODIFIER")
        box.template_icon_view(op, "estilo", show_labels=True, scale=7.0, scale_popup=9.0)
        if op.estilo not in ("NONE", "__GLOBAL__", "__NONE__"):
            col = box.column(align=True)
            col.use_property_split = True
            col.use_property_decorate = False
            col.prop(op, "guardar_como")
    by_name = {}
    for f in op._fields:
        by_name[f["p"]] = f
    for title, flist, opt in spec["groups"]:
        names = []
        for f in flist:
            for p in f["_p"]:
                fld = by_name[p]
                if ((fld["g"] and not fld["w"] and faces and not spec.get("roof"))
                        or (fld["w"] and not fld["g"] and not faces)):
                    continue          # el campo no existe en este alcance
                if f.get("vis") is not None:
                    try:
                        if not f["vis"](op):
                            continue
                    except Exception:
                        _log_exc("visibilidad_campo")
                names.append(p)
        if not names:
            continue
        box = lay.box()
        use = None
        if opt and faces and any(l and g == opt for g, _u, l in spec.get("wall_groups", [])):
            use = next(u for g, u, l in spec.get("wall_groups", []) if g == opt)
        if use:
            box.prop(op, "usar_" + use, text=title)
        else:
            box.label(text=title)
        col = box.column(align=True)
        col.use_property_split = True            # el título afuera (a la izquierda) y el campo a la derecha
        col.use_property_decorate = False
        if use:
            col.enabled = bool(getattr(op, "usar_" + use))
        for p in names:
            col.prop(op, p)
    if spec.get("roof") and faces:
        col = lay.column(align=True)
        col.use_property_split = True
        col.use_property_decorate = False
        col.prop(op, "volver_general")
    if spec.get("style"):
        kind = spec["style"]
        if op.estilo not in ("NONE", "__GLOBAL__", "__NONE__"):
            for title, names, _c in _style_groups(kind):
                vis = [n for n in names if _style_vis(op, n)]
                if not vis:
                    continue
                box = lay.box()
                box.label(text=title)
                col = box.column(align=True)
                col.use_property_split = True
                col.use_property_decorate = False
                for n in vis:
                    col.prop(op, n)


class _VisProxy:
    """Objeto para las condiciones `vis` de los campos: lee de la pared (caras elegidas) o del edificio, como el cuadro de diálogo."""

    def __init__(self, s, w, faces):
        self._s, self._w = s, w
        self.alcance = "FACES" if faces else "ALL"

    def __getattr__(self, n):
        for src in ((self._w, self._s) if self.alcance == "FACES" else (self._s, self._w)):
            if hasattr(src, n):
                return getattr(src, n)
        raise AttributeError(n)


def _wall_use_of(prop):
    """La propiedad «use_…» del grupo de pared al que pertenece `prop` (o None si no depende de ninguno)."""
    for g in WALL_GROUPS.values():
        if prop in g["ints"] or prop in g["floats"]:
            return g["use"]
    return None


def _comp_section(lay, context, spec, editing, n):
    """Un componente como sección desplegable con sus valores a la vista: cada cambio se aplica (y se ve) enseguida."""
    s = context.scene.cs2_building
    w = context.scene.cs2_wall
    faces = editing and not spec.get("only_all")
    res = lay.panel("cs2_comp_" + spec["key"], default_closed=True)
    header, body = res if isinstance(res, tuple) else (res, res)
    header.label(text=spec["label"], icon=spec["icon"])
    if body is None:
        return
    if editing and spec.get("only_all"):
        ui_note(body, "Vale para todo el edificio (no por caras).")
    if faces and n == 0:
        body.label(text="Hacé clic en una o más caras de la forma", icon="INFO")
        return
    uses = {}
    if faces:
        for _gid, use, label in spec.get("wall_groups", []):
            body.prop(w, use, text=label or f"Personalizar {spec['label'].lower()} en estas caras")
            uses[use] = getattr(w, use)
    if spec.get("style"):
        kind = spec["style"]
        sb = body.column()
        sb.label(text="Estilo")
        if faces:
            draw_style_picker(sb, w, _KIND_WALL[kind], "wall")
        else:
            draw_style_picker(sb, s, _KIND_PROP[kind], "building")
    proxy = _VisProxy(s, w, faces)
    for title, fields, _cond in spec["groups"]:
        shown = []
        for f in fields:
            attr = f["w"] if faces else f["g"]
            if not attr:
                continue
            vis = f.get("vis")
            try:
                if vis and not vis(proxy):
                    continue
            except Exception:
                pass
            shown.append(attr)
        if not shown:
            continue
        col = body.column(align=True)
        col.label(text=title)
        data = w if faces else s
        for attr in shown:
            r = col.row(align=True)
            use = _wall_use_of(attr) if faces else None
            if use and not uses.get(use, getattr(w, use, True)):
                r.active = False
            r.prop(data, attr)
    if faces and spec.get("roof"):
        ui_note(body, "El techo de caras sueltas se arma en «Más opciones…».")
    body.operator(f"cs2.comp_{spec['key']}", text="Más opciones…" if not spec.get("style") else "Editar el estilo y más opciones…",
                  icon="PREFERENCES")


class CS2_PT_comp(bpy.types.Panel):
    bl_label = "Componentes"
    bl_idname = "CS2_PT_comp"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "CS2"

    poll = _tab_poll("PERS")

    def draw(self, context):
        s = context.scene.cs2_building
        lay = self.layout
        editing, n = _faces_state(context)
        box = lay.box()
        if not editing:
            box.label(text="Editando: todo el edificio", icon="HOME")
            ui_note(box, "Cada cambio vale para todo el edificio.")
            if _has_shape(s):
                box.operator("cs2.edit_shape", text="Elegir caras para editar solo esas", icon="SNAP_FACE")
        else:
            box.label(text=f"Editando: {n} cara(s) elegida(s)" if n else "Editando: caras elegidas", icon="SNAP_FACE")
            ui_note(box, "Cada cambio vale solo para esas caras." if n else "Hacé clic en una o más caras de la forma.")
            box.operator("cs2.edit_shape", text="Volver a todo el edificio", icon="HOME")
        if editing and n:
            row = lay.row(align=True)
            row.label(text="Elegir paredes de material:")
            for k_, lab_ in (("1", "A"), ("2", "B"), ("3", "C"), ("4", "D")):
                row.operator("cs2.sel_wall_mat", text=lab_).mat = k_
        if not editing:                 # tipos de fachada y estilos de una vez (valen para todo el edificio)
            res = lay.panel("cs2_facade_types", default_closed=False)
            fh, fbox = res if isinstance(res, tuple) else (res, res)
            fh.label(text="Tipos de fachada", icon="OUTLINER_OB_MESH")
            if fbox is not None:
                grid = fbox.grid_flow(columns=2, align=True)
                for k, v in FACADE_TYPES.items():
                    grid.operator("cs2.facade_type", text=v[0]).kind = k
                row = fbox.row(align=True)
                for k, v in LOOK_PRESETS.items():
                    row.operator("cs2.look", text=v[0].replace("Casa moderna: ", ""), icon="HOME").look = k
        row = lay.row(align=True)
        row.prop(s, "auto_refresh", icon="FILE_REFRESH")
        if not s.auto_refresh or _PENDING[0]:
            sub = row.row(align=True)
            sub.alert = bool(_PENDING[0])
            sub.operator("cs2.generate", text="Actualizar ahora" + (" •" if _PENDING[0] else ""), icon="FILE_REFRESH")
        for spec in _COMP["specs"].values():
            _comp_section(lay, context, spec, editing, n)



def _register_components():
    for spec in _componentes():
        cls = _make_component(spec)
        bpy.utils.register_class(cls)
        _COMP["classes"].append(cls)
        _COMP["specs"][spec["key"]] = spec


def _unregister_components():
    for cls in reversed(_COMP["classes"]):
        try:
            bpy.utils.unregister_class(cls)
        except Exception:
            pass
    _COMP["classes"].clear()
    _COMP["specs"].clear()



# ----------------------------------------------------------------------------------------------------------------
# Editor de fachada por ranuras: cada abertura es un elemento editable de su pared (borrar, mover, cambiar tipo, medidas y estilo,
# agregar). Las ediciones se guardan en el objeto forma (cs2_slots) con la clave geométrica de la pared, así sobreviven al regenerar.
# ----------------------------------------------------------------------------------------------------------------
def _chain_slot_key(ch, center):
    fs = ch["facets"]
    return [round(fs[0]["S"][0] + center.x, 3), round(fs[0]["S"][1] + center.y, 3),
            round(fs[-1]["E"][0] + center.x, 3), round(fs[-1]["E"][1] + center.y, 3),
            round(ch["zmin"] + center.z, 3), round(ch["zmax"] + center.z, 3)]


def slot_layout(p, shape, idx):
    """Reparto automático de la pared (o curva entera) número `idx` de `shape`, sin las ediciones: las ranuras con su piso (f), columna
    (k; la puerta es -1), tipo, medidas y el cuadro de la pared. Es lo que el editor dibuja y a lo que se refieren las ediciones."""
    pts, n, cfg = shape["walls"][idx]
    cfg = cfg or {}
    center = shape["center"]
    ch = cfg.get("_chain")
    base = {k: v for k, v in cfg.items() if k != "_slots"}
    p = dict(p)
    p.pop("_chain_cache", None)
    if ch:
        key = _chain_slot_key(ch, center)
        _chain_virtual(p, base, ch, 0)
    else:
        key = _slot_key_of(pts, n, center)
        u = Vector((0, 0, 1)).cross(n).normalized()
        poly = [((q - pts[0]).dot(u), q.z) for q in pts]
        shape_openings(p, poly, n, idx, 0, base)
    last = p.get("_slot_last") or {}
    slots = []
    for op, tg in zip(last.get("ops", []), last.get("tags", [])):
        if tg:
            slots.append(dict(f=tg[0], k=tg[1], kind=_slot_kind_of(op, last["bals"]), a0=op[0], a1=op[1], z0=op[2], z1=op[3]))
    lay = dict(key=key, chain=bool(ch), slots=slots, defaults={k: last.get(k) for k in ("ww", "wh", "sill", "dw", "dh", "fh")},
               amin=last.get("amin", 0.0), amax=last.get("amax", 0.0), zmin=last.get("zmin", 0.0), zmax=last.get("zmax", 0.0))
    fh = last.get("fh") or 3.0
    lay["nfl"] = max(1, int(round((lay["zmax"] - lay["zmin"]) / fh)))
    return lay


def slot_entry_for(data, key, create=False):
    e = slot_edits_match(data, key)
    if e is None and create:
        e = {"key": list(key), "ov": {}, "add": []}
        data["walls"].append(e)
    return e


def slot_edits_save(obj, data):
    data["walls"] = [w for w in data["walls"] if w.get("ov") or w.get("add")]
    if data["walls"]:
        obj["cs2_slots"] = json.dumps(data)
    elif "cs2_slots" in obj:
        del obj["cs2_slots"]


def slot_scope_tags(lay, tag, scope):
    """Ranuras a las que vale una edición: esta, toda su columna, todo su piso o toda la pared. tag = (f, k) o ("add", i)."""
    if tag[0] == "add" or scope == "SLOT":
        return [tag]
    sl = lay["slots"]
    if scope == "COLUMN":
        return [(x["f"], x["k"]) for x in sl if x["k"] == tag[1]]
    if scope == "FLOOR":
        return [(x["f"], x["k"]) for x in sl if x["f"] == tag[0]]
    return [(x["f"], x["k"]) for x in sl]


def slot_set_fields(entry, tags, fields):
    """Escribe campos de edición en esas ranuras (un valor None quita el campo)."""
    for tg in tags:
        if tg[0] == "add":
            if 0 <= tg[1] < len(entry["add"]):
                tgt = entry["add"][tg[1]]
            else:
                continue
        else:
            tgt = entry["ov"].setdefault(f"{tg[0]},{tg[1]}", {})
        for k, v in fields.items():
            if v is None:
                tgt.pop(k, None)
            else:
                tgt[k] = v
    entry["ov"] = {k: v for k, v in entry["ov"].items() if v}


def slot_effective(lay, entry, tag):
    """Valores con los que se ve una ranura (reparto automático más ediciones): dict kind, w, h, sill, da, style, a0, a1, f, existe."""
    d = lay["defaults"]
    e = {}
    slot = None
    if tag[0] == "add":
        e = (entry or {}).get("add", [])[tag[1]] if entry and 0 <= tag[1] < len(entry.get("add", [])) else {}
        kind = e.get("kind", "win")
        w0, h0, s0 = _slot_defaults(kind, dict(fh=d["fh"], ww=d["ww"], wh=d["wh"], sill=d["sill"], dw=d["dw"], dh=d["dh"]))
        c = float(e.get("a", (lay["amin"] + lay["amax"]) / 2))
        f = int(e.get("f", 1))
    else:
        slot = next((x for x in lay["slots"] if x["f"] == tag[0] and x["k"] == tag[1]), None)
        if slot is None:
            return None
        e = ((entry or {}).get("ov") or {}).get(f"{tag[0]},{tag[1]}", {})
        kind = e.get("kind") or slot["kind"]
        w0, h0, s0 = slot["a1"] - slot["a0"], slot["z1"] - slot["z0"], slot["z0"] - (lay["zmin"] + slot["f"] * d["fh"])
        if kind != slot["kind"] and kind != "none":
            w0, h0, s0 = _slot_defaults(kind, dict(fh=d["fh"], ww=d["ww"], wh=d["wh"], sill=d["sill"], dw=d["dw"], dh=d["dh"]))
        c = (slot["a0"] + slot["a1"]) / 2
        f = slot["f"]
    w, h = float(e.get("w", w0)), float(e.get("h", h0))
    da = float(e.get("da", 0.0))
    return dict(kind=kind, w=w, h=h, sill=float(e.get("sill", s0)), da=da, style=e.get("style") or "__GLOBAL__",
                a0=c + da - w / 2, a1=c + da + w / 2, f=f, edited=bool(e))


_SLOT_CACHE = {"t": 0.0, "sig": None, "ctx": None}


def slot_cache_clear():
    _SLOT_CACHE.update(t=0.0, sig=None, ctx=None)


def _active_wall_ref(context):
    """(objeto forma, forma leída, índice de la pared) de la cara activa en Editar forma, o None."""
    obj = _edit_shape_obj(context)
    if obj is None:
        return None
    f = bmesh.from_edit_mesh(obj.data).faces.active
    if f is None or abs(f.normal.z) > 0.12:
        return None
    s = context.scene.cs2_building
    cw = obj.matrix_world @ f.calc_center_median()
    shape = read_shape(obj, s.shape_recalc, s.floor_height if s.snap_floors else None, s.curve_deg)
    best = None
    for i, (pts, n, _cfg) in enumerate(shape["walls"]):
        c = sum((q for q in pts), Vector((0, 0, 0))) / len(pts) + shape["center"]
        d = math.hypot(c.x - cw.x, c.y - cw.y)
        if n.dot(Vector((f.normal.x, f.normal.y, 0.0)).normalized()) > 0.9 and (best is None or d < best[0]):
            best = (d, i)
    if best is None or best[0] > 0.3 + 0.5 * max(shape["size"].x, shape["size"].y) * 0:
        return None
    return obj, shape, best[1]


def slot_ctx(context, fresh=False):
    """Todo lo que usa el editor de ranuras para la cara activa: dict(obj, s, p, lay, data, entry) o None. Guarda el resultado
    medio segundo para no recalcular en cada repintado de la interfaz."""
    ref = _active_wall_ref(context)
    if ref is None:
        return None
    obj, shape, idx = ref
    s = context.scene.cs2_building
    sig = (obj.name, idx, len(obj.data.vertices), obj.get("cs2_slots"), s.floor_height, s.curve_deg, s.win_w, s.win_h)
    now = time.time()
    if not fresh and _SLOT_CACHE["sig"] == sig and now - _SLOT_CACHE["t"] < 0.5 and _SLOT_CACHE["ctx"] is not None:
        return _SLOT_CACHE["ctx"]
    p = props_to_params(s)
    lay = slot_layout(p, shape, idx)
    data = slot_edits_load(obj)
    ctx = dict(obj=obj, s=s, p=p, lay=lay, data=data, entry=slot_edits_match(data, lay["key"]))
    _SLOT_CACHE.update(t=now, sig=sig, ctx=ctx)
    return ctx


def _slot_sel_tag(s):
    if s.ranura_add >= 0:
        return ("add", s.ranura_add)
    if s.ranura_f < 0:
        return None
    return (s.ranura_f, s.ranura_k)


_SLOT_LOCK = [False]
_SLOT_NONE_ITEMS = [("__GLOBAL__", "—", "")]


def _slot_style_items(self, context):
    kind = {"win": "window", "door": "door", "bal": "balcony"}.get(self.ranura_kind)
    if kind is None:
        return _SLOT_NONE_ITEMS
    return _items(kind, "__GLOBAL__", "Igual que el resto", ("__NONE__", "Sin estilo", ""))


def _slot_load_ui(context, ctx=None):
    """Copia a la interfaz los valores de la ranura elegida."""
    s = context.scene.cs2_building
    tag = _slot_sel_tag(s)
    ctx = ctx or slot_ctx(context, fresh=True)
    if ctx is None or tag is None:
        return
    ef = slot_effective(ctx["lay"], ctx["entry"], tag)
    if ef is None:
        return
    _SLOT_LOCK[0] = True
    try:
        s.ranura_kind = ef["kind"] if ef["kind"] in ("win", "door", "bal", "shop", "none") else "win"
        s.ranura_w, s.ranura_h, s.ranura_sill, s.ranura_da = ef["w"], ef["h"], ef["sill"], ef["da"]
        try:
            s.ranura_style = ef["style"]
        except TypeError:
            s.ranura_style = "__GLOBAL__"
    finally:
        _SLOT_LOCK[0] = False


def _slot_commit(context, ctx, fields, tags=None):
    """Escribe una edición en el objeto forma y avisa que hay cambios sin aplicar."""
    s = ctx["s"]
    tag = _slot_sel_tag(s)
    if tag is None:
        return
    entry = ctx["entry"] or slot_entry_for(ctx["data"], ctx["lay"]["key"], create=True)
    if tags is None:
        tags = slot_scope_tags(ctx["lay"], tag, s.ranura_scope)
    slot_set_fields(entry, tags, fields)
    slot_edits_save(ctx["obj"], ctx["data"])
    slot_cache_clear()
    _on_change(s, context)


def _slot_field_update(field):
    def upd(self, context):
        if _SLOT_LOCK[0]:
            return
        ctx = slot_ctx(context, fresh=True)
        if ctx is None or _slot_sel_tag(self) is None:
            return
        if field == "kind":
            fields = {"kind": self.ranura_kind, "w": None, "h": None, "sill": None, "style": None}
        elif field == "style":
            fields = {"style": None if self.ranura_style == "__GLOBAL__" else self.ranura_style}
        else:
            fields = {field: float(getattr(self, "ranura_" + field))}
        _slot_commit(context, ctx, fields)
        if field == "kind":
            _slot_load_ui(context)
    return upd


def _install_slot_props():
    ba = CS2BuildingProps.__annotations__
    ba["ranura_f"] = IntProperty(default=-1)
    ba["ranura_k"] = IntProperty(default=0)
    ba["ranura_add"] = IntProperty(default=-1)
    ba["ranura_scope"] = EnumProperty(name="Vale para", default="SLOT",
                                      items=[("SLOT", "Esta ranura", ""), ("COLUMN", "Su columna", "Todas las ranuras de la misma columna"),
                                             ("FLOOR", "Su piso", "Todas las ranuras del mismo piso"), ("WALL", "Toda la pared", "")])
    ba["ranura_kind"] = EnumProperty(name="Tipo", default="win", items=SLOT_KINDS, update=_slot_field_update("kind"))
    ba["ranura_style"] = EnumProperty(name="Estilo", items=_slot_style_items, update=_slot_field_update("style"))
    ba["ranura_w"] = FloatProperty(name="Ancho (m)", default=1.2, min=0.4, max=12.0, update=_slot_field_update("w"))
    ba["ranura_h"] = FloatProperty(name="Alto (m)", default=1.4, min=0.3, max=8.0, update=_slot_field_update("h"))
    ba["ranura_sill"] = FloatProperty(name="Antepecho (m)", default=0.9, min=0.0, max=6.0, update=_slot_field_update("sill"))
    ba["ranura_da"] = FloatProperty(name="Correr (m)", default=0.0, min=-12.0, max=12.0, update=_slot_field_update("da"),
                                    description="Mueve la ranura a lo largo de la pared respecto de su lugar automático")


_install_slot_props()


class CS2_OT_ranura_pick(bpy.types.Operator):
    bl_idname = "cs2.ranura_pick"
    bl_label = "Elegir ranura"
    bl_description = "Elige esta abertura para editarla"
    bl_options = {"INTERNAL"}
    f: IntProperty(default=-1)
    k: IntProperty(default=0)
    add: IntProperty(default=-1)

    def execute(self, context):
        s = context.scene.cs2_building
        s.ranura_f, s.ranura_k, s.ranura_add = self.f, self.k, self.add
        _slot_load_ui(context)
        return {"FINISHED"}


def _slot_op_ctx(op, context):
    ctx = slot_ctx(context, fresh=True)
    if ctx is None:
        op.report({"WARNING"}, "Elegí una pared en «Caras elegidas» (cara activa) para editar sus aberturas")
        return None
    if _slot_sel_tag(context.scene.cs2_building) is None:
        op.report({"WARNING"}, "Primero elegí una ranura de la grilla")
        return None
    return ctx


class CS2_OT_ranura_add(bpy.types.Operator):
    bl_idname = "cs2.ranura_add"
    bl_label = "Agregar a la derecha"
    bl_description = "Agrega una ventana nueva en el mismo piso, a la derecha de la ranura elegida"
    bl_options = {"REGISTER", "UNDO"}
    duplicar: BoolProperty(default=False)

    def execute(self, context):
        ctx = _slot_op_ctx(self, context)
        if ctx is None:
            return {"CANCELLED"}
        s = ctx["s"]
        lay = ctx["lay"]
        ef = slot_effective(lay, ctx["entry"], _slot_sel_tag(s))
        if ef is None:
            return {"CANCELLED"}
        w = ef["w"] if self.duplicar else lay["defaults"]["ww"]
        a = ef["a1"] + w / 2 + 0.25
        if a + w / 2 > lay["amax"] - 0.12:
            a = ef["a0"] - w / 2 - 0.25
        new = {"f": ef["f"], "a": round(a, 3), "kind": ef["kind"] if self.duplicar else "win"}
        if self.duplicar:
            new.update(w=round(ef["w"], 3), h=round(ef["h"], 3), sill=round(ef["sill"], 3))
            if ef["style"] != "__GLOBAL__":
                new["style"] = ef["style"]
        entry = ctx["entry"] or slot_entry_for(ctx["data"], lay["key"], create=True)
        entry["add"].append(new)
        slot_edits_save(ctx["obj"], ctx["data"])
        slot_cache_clear()
        s.ranura_f, s.ranura_k, s.ranura_add = -1, 0, len(entry["add"]) - 1
        _on_change(s, context)
        _slot_load_ui(context)
        return {"FINISHED"}


class CS2_OT_ranura_delete(bpy.types.Operator):
    bl_idname = "cs2.ranura_delete"
    bl_label = "Borrar"
    bl_description = "Saca la abertura (o las de su columna, piso o pared, según «Vale para»)"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        ctx = _slot_op_ctx(self, context)
        if ctx is None:
            return {"CANCELLED"}
        s = ctx["s"]
        tag = _slot_sel_tag(s)
        if tag[0] == "add":
            entry = ctx["entry"]
            if entry and 0 <= tag[1] < len(entry["add"]):
                entry["add"].pop(tag[1])
            slot_edits_save(ctx["obj"], ctx["data"])
            slot_cache_clear()
            s.ranura_f, s.ranura_k, s.ranura_add = -1, 0, -1
            _on_change(s, context)
            return {"FINISHED"}
        _slot_commit(context, ctx, {"kind": "none"})
        _slot_load_ui(context)
        return {"FINISHED"}


class CS2_OT_ranura_reset(bpy.types.Operator):
    bl_idname = "cs2.ranura_reset"
    bl_label = "Restablecer"
    bl_description = "Quita las ediciones de la ranura (o de su columna, piso o pared, según «Vale para») y vuelve al reparto automático"
    bl_options = {"REGISTER", "UNDO"}
    todo: BoolProperty(default=False, description="Quita todas las ediciones de la pared, incluidas las ranuras agregadas")

    def execute(self, context):
        ctx = slot_ctx(context, fresh=True)
        if ctx is None:
            self.report({"WARNING"}, "Elegí una pared en «Caras elegidas» (cara activa)")
            return {"CANCELLED"}
        s = ctx["s"]
        entry = ctx["entry"]
        if entry is None:
            return {"FINISHED"}
        tag = _slot_sel_tag(s)
        if self.todo or tag is None:
            ctx["data"]["walls"].remove(entry)
        elif tag[0] == "add":
            if 0 <= tag[1] < len(entry["add"]):
                entry["add"].pop(tag[1])
        else:
            for tg in slot_scope_tags(ctx["lay"], tag, s.ranura_scope):
                entry["ov"].pop(f"{tg[0]},{tg[1]}", None)
        slot_edits_save(ctx["obj"], ctx["data"])
        slot_cache_clear()
        if self.todo or (tag and tag[0] == "add"):
            s.ranura_f, s.ranura_k, s.ranura_add = -1, 0, -1
        _on_change(s, context)
        _slot_load_ui(context)
        return {"FINISHED"}


class CS2_OT_ranura_copy(bpy.types.Operator):
    bl_idname = "cs2.ranura_copy"
    bl_label = "Copiar ediciones"
    bl_description = "Copia las ediciones de ranuras de esta pared para pegarlas en otra"

    def execute(self, context):
        ctx = slot_ctx(context, fresh=True)
        if ctx is None:
            self.report({"WARNING"}, "Elegí una pared en «Caras elegidas» (cara activa)")
            return {"CANCELLED"}
        e = ctx["entry"] or {}
        context.window_manager["cs2_ranuras_copiadas"] = json.dumps({"ov": e.get("ov") or {}, "add": e.get("add") or []})
        self.report({"INFO"}, "Ediciones copiadas" if (e.get("ov") or e.get("add")) else "Esta pared no tiene ediciones: se copia el reparto automático")
        return {"FINISHED"}


class CS2_OT_ranura_paste(bpy.types.Operator):
    bl_idname = "cs2.ranura_paste"
    bl_label = "Pegar ediciones"
    bl_description = "Reemplaza las ediciones de ranuras de esta pared por las copiadas (las que no entren en esta pared se ignoran)"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        ctx = slot_ctx(context, fresh=True)
        raw = context.window_manager.get("cs2_ranuras_copiadas")
        if ctx is None or not raw:
            self.report({"WARNING"}, "Primero copiá las ediciones de otra pared y elegí esta con una cara activa")
            return {"CANCELLED"}
        clip = json.loads(raw)
        entry = ctx["entry"]
        if entry is not None:
            ctx["data"]["walls"].remove(entry)
        ctx["data"]["walls"].append({"key": list(ctx["lay"]["key"]), "ov": clip.get("ov") or {}, "add": clip.get("add") or []})
        slot_edits_save(ctx["obj"], ctx["data"])
        slot_cache_clear()
        s = ctx["s"]
        s.ranura_f, s.ranura_k, s.ranura_add = -1, 0, -1
        _on_change(s, context)
        return {"FINISHED"}


_SLOT_SYM = {"win": "V", "door": "P", "bal": "B", "shop": "T", "none": "×"}


def slot_grid(lay, entry):
    """Cuadro del editor: (columnas [(centro, ("k", k) | ("add", i))], celdas {(piso, clave de columna): dict(kind, edited, tag)})."""
    cols, seen, cells = [], set(), {}
    ov = (entry or {}).get("ov") or {}
    for x in lay["slots"]:
        key = ("k", x["k"])
        if key not in seen:
            seen.add(key)
            cols.append(((x["a0"] + x["a1"]) / 2, key))
        e = ov.get(f"{x['f']},{x['k']}")
        cells[(x["f"], key)] = dict(kind=(e or {}).get("kind") or x["kind"], edited=bool(e), tag=(x["f"], x["k"]))
    for i, e in enumerate((entry or {}).get("add") or []):
        key = ("add", i)
        cols.append((float(e.get("a", 0.0)), key))
        cells[(int(e.get("f", 1)), key)] = dict(kind=e.get("kind", "win"), edited=True, tag=("add", i))
    cols.sort(key=lambda c: c[0])
    return cols, cells


class CS2_PT_ranuras(bpy.types.Panel):
    bl_label = "Aberturas de la pared (ranuras)"
    bl_idname = "CS2_PT_ranuras"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "CS2"

    poll = _tab_poll("PERS")

    def draw(self, context):
        s = context.scene.cs2_building
        lay = self.layout
        editing, n = _faces_state(context)
        if not editing or n == 0:
            ui_note(lay, "Para editar las aberturas de una pared: «Elegir caras para editar solo esas» (arriba), "
                         "hacé clic en la pared y acá aparece su grilla de ventanas y puertas.")
            return
        ctx = slot_ctx(context)
        if ctx is None:
            ui_note(lay, "Hacé clic en una pared de la forma (cara vertical) para ver sus aberturas.", icon="INFO")
            return
        L, entry = ctx["lay"], ctx["entry"]
        lay.operator("cs2.generate", text="Actualizar edificio" + (" •" if _PENDING[0] else ""), icon="FILE_REFRESH")
        ui_note(lay, f"{'Curva' if L['chain'] else 'Pared'} de {L['amax'] - L['amin']:.1f} m · {L['nfl']} piso(s). "
                     "V ventana · P puerta · B balcón · T vidriera · × borrada · * editada")
        cols, cells = slot_grid(L, entry)
        sel = _slot_sel_tag(s)
        if sel not in {c["tag"] for c in cells.values()}:
            sel = None               # la ranura elegida era de otra pared
        grid = lay.grid_flow(row_major=True, columns=len(cols) + 1, even_columns=True, even_rows=True, align=True)
        for f in reversed(range(L["nfl"])):
            grid.label(text="PB" if f == 0 else f"P{f}")
            for _c, key in cols:
                cell = cells.get((f, key))
                if cell is None:
                    grid.label(text="·")
                    continue
                op = grid.operator("cs2.ranura_pick", text=_SLOT_SYM.get(cell["kind"], "?") + ("*" if cell["edited"] else ""),
                                   depress=(sel == cell["tag"]))
                if cell["tag"][0] == "add":
                    op.f, op.k, op.add = -1, 0, cell["tag"][1]
                else:
                    op.f, op.k, op.add = cell["tag"][0], cell["tag"][1], -1
        row = lay.row(align=True)
        row.operator("cs2.ranura_copy", text="Copiar", icon="COPYDOWN")
        row.operator("cs2.ranura_paste", text="Pegar", icon="PASTEDOWN")
        if sel is None:
            ui_note(lay, "Elegí una ranura de la grilla para editarla.")
            return
        box = lay.box()
        if sel[0] != "add":
            box.prop(s, "ranura_scope", text="Vale para")
        box.prop(s, "ranura_kind")
        if s.ranura_kind in ("win", "door", "bal"):
            box.prop(s, "ranura_style")
        if s.ranura_kind != "none":
            col = box.column(align=True)
            for nm in ("ranura_w", "ranura_h") + (("ranura_sill",) if s.ranura_kind in ("win", "shop") else ()) + ("ranura_da",):
                col.prop(s, nm)
        row = box.row(align=True)
        row.operator("cs2.ranura_add", text="Agregar ventana", icon="ADD").duplicar = False
        row.operator("cs2.ranura_add", text="Duplicar", icon="DUPLICATE").duplicar = True
        row = box.row(align=True)
        row.operator("cs2.ranura_delete", icon="TRASH")
        row.operator("cs2.ranura_reset", icon="LOOP_BACK").todo = False
        lay.operator("cs2.ranura_reset", text="Restablecer toda la pared", icon="X").todo = True


classes = (CS2BuildingProps, CS2WallProps, CS2_OT_generate, CS2_OT_cancel_generate, CS2_OT_check_holes, CS2_OT_random_seed, CS2_OT_sample_pieces,
           CS2_OT_export, CS2_OT_regen_lods, CS2_OT_from_mesh, CS2_OT_cs1_generate, CS2_OT_export_cs1, CS2_OT_clean_mesh, CS2_OT_grid, CS2_OT_batch, CS2_OT_create_shape, CS2_OT_facade_type, CS2_OT_delete_all, CS2_OT_reset,
           CS2_OT_edit_shape, CS2_OT_floor, CS2_OT_split_floors, CS2_OT_select_band,
           CS2_OT_wall_reset, CS2_OT_style_step, CS2_OT_style_folder, CS2_OT_style_reload,
           CS2_OT_thumbs_all, CS2_OT_refresh, CS2_OT_look, CS2_OT_porch, CS2_OT_roof_apply, CS2_OT_roof_clear, CS2_OT_pbr_pick, CS2_OT_pbr_folder, CS2_OT_pbr_clear, CS2_OT_atlas_view, CS2_OT_make_textures,
           CS2_OT_uv_generate, CS2_OT_bake_ao, CS2_OT_tex_assign, CS2_OT_slot_rot, CS2_OT_sel_wall_mat, CS2_OT_curve_select, CS2_OT_tex_clear, CS2_OT_copy_report, CS2_OT_retexture, CS2StyleProps, CS2_OT_style_load, CS2_OT_style_save, CS2_OT_style_delete, CS2_OT_style_use,
           CS2_OT_style_preview, CS2_OT_style_catalog, CS2_OT_style_export, CS2_OT_style_import, CS2_OT_style_refresh, CS2_OT_style_apply,
           CS2_OT_ficha_clip, CS2_OT_ficha_file, CS2_OT_ficha_prompt, CS2_OT_type_template,
           CS2_OT_preview_update, CS2_OT_uv_zone, CS2_OT_uv_pin, CS2_OT_uv_view,
           CS2_PT_panel,
           CS2_PT_forma,
           CS2_OT_ranura_pick, CS2_OT_ranura_add, CS2_OT_ranura_delete, CS2_OT_ranura_reset, CS2_OT_ranura_copy, CS2_OT_ranura_paste,
           CS2_PT_comp, CS2_PT_ranuras, CS2_PT_colores,
           CS2_PT_tex, CS2_PT_mats, CS2_PT_uv, CS2_PT_bake,
           CS2_PT_export, CS2_PT_export_go, CS2_PT_ficha)


def _split_panel(cls):
    """Los campos del panel se dibujan con el título a la izquierda y el valor a la derecha."""
    orig = cls.draw

    def draw(self, context):
        self.layout.use_property_split = True
        self.layout.use_property_decorate = False
        orig(self, context)
    cls.draw = draw


for _c in (CS2_PT_tex, CS2_PT_mats, CS2_PT_uv, CS2_PT_bake, CS2_PT_export, CS2_PT_export_go, CS2_PT_ficha):
    _split_panel(_c)


@bpy.app.handlers.persistent
def _load_post_handler(*_a):
    """Al abrir un .blend: el edificio solo se regenera con el botón Generar."""
    try:
        for sc in bpy.data.scenes:
            if hasattr(sc, "cs2_building"):
                sc.cs2_building.live_update = False
    except Exception:
        pass


def register():
    for c in classes:
        bpy.utils.register_class(c)
    bpy.types.Scene.cs2_building = PointerProperty(type=CS2BuildingProps)
    bpy.types.Scene.cs2_wall = PointerProperty(type=CS2WallProps)
    bpy.types.Scene.cs2_style = PointerProperty(type=CS2StyleProps)
    lib_refresh()
    _register_components()
    for _h in ("VIEW3D_HT_tool_header",):
        _cls = getattr(bpy.types, _h, None)
        if _cls is not None:
            try:
                _cls.remove(_header_draw)
            except Exception:
                pass
            _cls.append(_header_draw)
    if _depsgraph_handler not in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.append(_depsgraph_handler)
    if _load_post_handler not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(_load_post_handler)
    _load_post_handler()


def unregister():
    for _c in (_TILE_CACHE, _PERIOD_CACHE, _LIB_CACHE):
        _c.clear()
    _TMP_MESHES.clear()
    _MATPREV["cache"].clear()
    if _MATPREV["pcoll"] is not None:
        bpy.utils.previews.remove(_MATPREV["pcoll"])
        _MATPREV["pcoll"] = None
    _hdr = getattr(bpy.types, "VIEW3D_HT_tool_header", None)
    if _hdr is not None:
        try:
            _hdr.remove(_header_draw)
        except Exception:
            pass
    if _depsgraph_handler in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.remove(_depsgraph_handler)
    if _load_post_handler in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_load_post_handler)
    _unregister_components()
    del bpy.types.Scene.cs2_style
    del bpy.types.Scene.cs2_wall
    del bpy.types.Scene.cs2_building
    for c in reversed(classes):
        bpy.utils.unregister_class(c)


if __name__ == "__main__":
    try:
        unregister()
    except Exception:
        pass
    register()
