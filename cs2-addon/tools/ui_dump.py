"""Vuelca como texto lo que muestra cada pestaña del add-on (sin abrir Blender), para revisar la interfaz.
Uso: python3 tools/ui_dump.py [salida.txt]"""
import os
import sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tests"))
from common import a, bpy, setup  # noqa: E402

OUT = []


class Rec:
    def __init__(self, depth=0, kind="root"):
        self.depth, self.kind = depth, kind
        self.scale_x = self.scale_y = 1
        self.enabled = self.active = True
        self.alert = False
        self.use_property_split = self.use_property_decorate = False
        self.alignment = "EXPAND"

    def _w(self, txt):
        OUT.append("  " * self.depth + txt)

    def _child(self, kind, heading=""):
        if kind == "box":
            self._w("┌ caja" + (f" «{heading}»" if heading else ""))
        return Rec(self.depth + (1 if kind == "box" else 0), kind)

    def box(self, *a, **k):
        return self._child("box")

    def row(self, *a, **k):
        return self._child("row", k.get("heading", ""))

    def column(self, *a, **k):
        return self._child("col", k.get("heading", ""))

    def split(self, *a, **k):
        return self._child("split")

    def grid_flow(self, *a, **k):
        return self._child("grid")

    def column_flow(self, *a, **k):
        return self._child("grid")

    def panel(self, idname, *a, **k):
        self._w(f"▸ subpanel {idname}")
        return (Rec(self.depth + 1), Rec(self.depth + 1))

    def label(self, text="", icon="NONE", **k):
        if text:
            self._w(f"· {text}")

    def separator(self, *a, **k):
        pass

    def separator_spacer(self, *a, **k):
        pass

    def prop(self, data, prop, text=None, **k):
        try:
            name = data.bl_rna.properties[prop].name
        except Exception:
            name = prop
        self._w(f"[{text if text not in (None, '') else name}]" + ("" if text != "" else " (sin texto)"))
        return Rec(self.depth)

    def prop_enum(self, data, prop, value, text=None, **k):
        self._w(f"(opción {text or value})")

    def operator(self, idname, text=None, **k):
        self._w(f"<botón {text if text is not None else idname}>")
        return type("Op", (), {"__setattr__": lambda s, n, v: object.__setattr__(s, n, v)})()

    def menu(self, *a, text="", **k):
        self._w(f"<menú {text}>")

    def template_icon_view(self, *a, **k):
        self._w("<miniaturas>")

    def template_list(self, *a, **k):
        self._w("<lista>")

    def __getattr__(self, n):
        if n.startswith("__"):
            raise AttributeError(n)
        return lambda *a, **k: Rec(self.depth)

    def __bool__(self):
        return True


def dump(tab, with_building):
    s = bpy.context.scene.cs2_building
    s.ui_tab = tab
    panels = [c for c in a.classes if isinstance(c, type) and issubclass(c, bpy.types.Panel)]
    OUT.append(f"\n========== PESTAÑA {tab} ({'con' if with_building else 'sin'} edificio) ==========")
    for cls in panels:
        poll = getattr(cls, "poll", None)
        if poll is not None and not poll(bpy.context):
            continue
        OUT.append(f"\n## {cls.bl_label} ({cls.__name__})")
        inst = type("X", (), {})()
        inst.layout = Rec()
        try:
            cls.draw(inst, bpy.context)
        except Exception as e:
            OUT.append(f"!! error al dibujar: {e!r}")


if __name__ == "__main__":
    a.register()
    for tab in ("FORMA", "PERS", "TEX", "EXPORT"):
        dump(tab, False)
    setup("L", "GABLE_TILE", 24, 16, 4)
    for tab in ("FORMA", "PERS", "TEX", "EXPORT"):
        dump(tab, True)
    out = sys.argv[1] if len(sys.argv) > 1 else "ui_dump.txt"
    open(out, "w", encoding="utf-8").write("\n".join(OUT))
    print("líneas", len(OUT))
    sys.stdout.flush()
    os._exit(0)
