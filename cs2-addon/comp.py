# ----------------------------------------------------------------------------
# Componentes: un botón y un panel de ajustes aparte por cada elemento personalizable
# (idea de building_tools: cada herramienta abre su propia ventana de ajustes con los valores en vivo).
# Alcance: «Todo el edificio» o «Caras elegidas» (mismo selector de siempre, sin usar el modo edición como medio de trabajo).
# ----------------------------------------------------------------------------
_COMP = {"classes": [], "specs": {}}


def _rna(owner, attr):
    return getattr(bpy.types, owner).bl_rna.properties.get(attr)


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
        slo, shi = max(lo, a.ui_range[0]), min(hi, a.ui_range[1])
        kw.update(default=a.default, min=lo, max=hi, soft_min=slo, soft_max=shi)
        if a.type == "FLOAT":
            kw.update(precision=a.precision, subtype=a.subtype, unit=a.unit)
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
    return [k for k in STYLE_DEFAULT if k not in _ST_SKIP]


def _style_groups(kind):
    """Grupos de campos del estilo: (título, [campos], condición)."""
    sc = lambda *n: [("e_" + x) for x in n]
    if kind == "balcony":
        return [
            ("Planta y losa", sc("bal_type", "bal_front", "bal_depth", "bal_extra_w", "bal_slab_t", "bal_support", "bal_bay_h"), None),
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
        dict(key="curtain", label="Muro cortina", icon="MOD_WIREFRAME",
             desc="Muro cortina de vidrio: módulos, perfiles, franjas y aletas",
             wall_groups=[("cw", "use_cw", None)], wall_modes=[("cs2_k_mode", "cw_mode")],
             groups=[("Muro cortina", [_F("cw_on_g", None), _F(None, "cw_mode")], None),
                     ("Módulos y perfiles", [_F("cw_mw_g", "cw_mw"), _F("cw_mh_g", "cw_mh"), _F("cw_pt_g", "cw_pt"),
                                             _F("cw_pd_g", "cw_pd"), _F("cw_mmat_g", "cw_mmat")], None),
                     ("Franjas y bordes", [_F("cw_band_g", "cw_band"), _F("cw_bmat_g", "cw_bmat"), _F("cw_edge_g", "cw_edge")], None),
                     ("Aletas", [_F("cw_finn_g", "cw_finn"),
                                 _F("cw_find_g", "cw_find", vis=lambda o: _cw_fins(o)),
                                 _F("cw_fint_g", "cw_fint", vis=lambda o: _cw_fins(o)),
                                 _F("cw_fmat_g", "cw_fmat", vis=lambda o: _cw_fins(o))], None)]),
        dict(key="walls", label="Paredes", icon="MATERIAL",
             desc="Acabados de pared: materiales, paños de color, marcos salientes y lamas",
             wall_groups=[("pan", "use_pan", "Paños de color"), ("cas", "use_cas", "Marcos salientes"),
                          ("slt", "use_slt", "Lamas")],
             wall_modes=[("cs2_f_mat", "f_mat")],
             groups=[("Materiales", [_F("wall_mat_g", "f_mat"), _F("ground_mat_g", None),
                                     _F("ground_n_g", None, vis=lambda o: o.ground_mat_g != "0"), _F("frm_mat_g", None)], None),
                     ("Paños de color alrededor de ventanas",
                      [_F("pan_mode_g", "pan_mode"), _F("pan_mat_g", "pan_mat", vis=lambda o: _pan_on(o)),
                       _F("pan_pad_g", "pan_pad", vis=lambda o: _pan_on(o)),
                       _F("pan_prob_g", "pan_prob", vis=lambda o: _pan_on(o) and _pan_mode(o) == "2")], "pan"),
                     ("Marcos salientes en ventanas",
                      [_F("cas_w_g", "cas_w"), _F("cas_d_g", "cas_d", vis=lambda o: _cas_on(o)),
                       _F("cas_mat_g", "cas_mat", vis=lambda o: _cas_on(o))], "cas"),
                     ("Lamas (madera, aluminio)",
                      [_F("slt_mode_g", "slt_mode"), _F("slt_gap_g", "slt_gap", vis=lambda o: _slt_on(o)),
                       _F("slt_w_g", "slt_w", vis=lambda o: _slt_on(o)), _F("slt_d_g", "slt_d", vis=lambda o: _slt_on(o)),
                       _F("slt_mat_g", "slt_mat", vis=lambda o: _slt_on(o))], "slt")]),
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


def _comp_invoke_state(op, context, spec):
    s = context.scene.cs2_building
    w = context.scene.cs2_wall
    op.alcance = "ALL" if spec.get("only_all") else s.edit_scope
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
    if spec.get("roof"):
        op.r_preset_cargado = s.r_preset
    for _gid, use, label in spec.get("wall_groups", []):
        if label:
            try:
                setattr(op, "usar_" + use, True)
            except Exception:
                pass


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
        return self.execute(context)

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
        "bl_options": {"REGISTER", "UNDO"}, "__annotations__": ann,
        "_fields": fields, "invoke": invoke, "execute": execute, "draw": draw,
        "poll": classmethod(lambda cls, context: getattr(context.scene, "cs2_building", None) is not None),
    })
    return cls


def _comp_draw(op, context, spec):
    lay = op.layout
    s = context.scene.cs2_building
    if not spec.get("only_all"):
        lay.prop(op, "alcance", expand=True)
    else:
        lay.label(text="Vale para todo el edificio", icon="HOME")
    faces = op.alcance == "FACES" and not spec.get("only_all")
    if faces:
        editing, n = _faces_state(context)
        if not editing:
            lay.label(text="Entrá en «Editar forma» y elegí caras", icon="ERROR")
        elif n == 0:
            lay.label(text="Hacé clic en una o más caras de la forma", icon="ERROR")
        else:
            lay.label(text=f"{n} cara(s) elegida(s)", icon="SNAP_FACE")
    if spec.get("porch"):
        lay.prop(op, "modelo")
    if spec.get("style"):
        box = lay.box()
        box.label(text="Estilo", icon="MODIFIER")
        box.prop(op, "estilo", text="")
        if op.estilo not in ("NONE", "__GLOBAL__", "__NONE__"):
            box.prop(op, "guardar_como")
    by_name = {}
    for f in op._fields:
        by_name[f["p"]] = f
    for title, flist, opt in spec["groups"]:
        names = []
        for f in flist:
            for p in f["_p"]:
                fld = by_name[p]
                if (fld["g"] and not fld["w"] and faces) or (fld["w"] and not fld["g"] and not faces):
                    continue          # el campo no existe en este alcance
                if f.get("vis") is not None:
                    try:
                        if not f["vis"](op):
                            continue
                    except Exception:
                        pass
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
        if use:
            col.enabled = bool(getattr(op, "usar_" + use))
        for p in names:
            col.prop(op, p)
    if spec.get("roof") and faces:
        lay.prop(op, "volver_general")
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
                for n in vis:
                    col.prop(op, n)


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
        ui_note(lay, "Cada botón abre su ventana de ajustes (abajo a la izquierda de la vista 3D) y rehace el componente en vivo.")
        _draw_scope(lay, context, s)
        if s.edit_scope == "FACES" and not _edit_shape_obj(context):
            lay.operator("cs2.edit_shape", text="Entrar a elegir caras", icon="EDITMODE_HLT")
        col = lay.column(align=True)
        for spec in _COMP["specs"].values():
            row = col.row(align=True)
            row.operator(f"cs2.comp_{spec['key']}", text=spec["label"], icon=spec["icon"])


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


