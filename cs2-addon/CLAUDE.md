# CS2 Generador de Edificios (add-on de Blender)

Add-on de un solo archivo (`cs2_edificio_addon.py`, v2.78, ~15 700 líneas) que genera edificios low-poly
para Cities: Skylines 2 (y CS1) con LODs, UV, texturas y exportación FBX. Blender 4.2+.

## Cómo trabajar con Leo
- Responder SIEMPRE en español rioplatense (voseo). Leo vive en Buenos Aires.
- Avisarle de las decisiones que le tocan a él, con una recomendación.
- Es de uso personal y está hecho con ayuda de IA; el canal de YouTube "Leal" lo muestra.
- Toda la interfaz del add-on está en español.

## Pruebas (headless, sin abrir Blender)
    pip install bpy numpy pillow
    python3 tests/run_tests.py                 # todo, ~11 min, 43 casos
    CS2_PERF_SCALE=2 python3 tests/run_tests.py perf_big   # si el equipo es el doble de lento que el de Leo
    python3 tests/run_tests.py roof_join scope_sync
    python3 tests/run_tests.py --update-baselines   # solo si la mejora es a propósito
- Los tests importan `cs2_edificio_addon` desde la carpeta raíz. Ver `tests/README.md`.
- `mip_bleed` a veces falla en la corrida completa y pasa solo.
- Cada caso sale con `os._exit` apenas termina (Blender a veces se cuelga al cerrar).
- Si hay una biblioteca EGL instalada en el sistema (`libEGL.so.1`), `bpy` se cuelga al redibujar sin ventana (rompe `progress`).
  Para renderizar imágenes se deja EGL en `/opt/egl` y solo se usa con
  `LD_LIBRARY_PATH=/opt/egl EGL_PLATFORM=surfaceless LIBGL_ALWAYS_SOFTWARE=1` (ver «Herramientas de revisión»).
- Antes de dar algo por arreglado: agregar un test que FALLE con el código viejo.

## Mapa del código
- `roof_build` (~línea 7000+): techos; rectángulos de `roof_rects`, vecinos con `_roof_neighbors`,
  voladizos `ov_m/ov_p`, relleno de hastiales (`ends`).
- Alcance "Todo el edificio / Caras elegidas": `_scope_update`, `_depsgraph_handler`, `_comp_invoke_state`.
- Paneles `CS2_PT_*` (25), operadores `CS2_OT_*` (~59). Exportación: `CS2_PT_export`, `CS2_PT_export_go`.
- Registro de errores: `_ERRLOG` + "Copiar informe".

## Texturas: giro y material por pared (v2.77)
- Giro por material: `ms_<material>_rot` (grados). `finish_bmesh` gira las coordenadas planas de cada pared o techo antes de repartir el
  atlas (`face_uv_turn`, `_uv_turn`), así que el giro llega solo a los tres LODs, a la vista rápida y al horneado. Con 90°/270° el piso se
  repite sobre u: `props_to_params` estira `_tx` en vez de `_ty` (`slot_tile_y(..., axis=0)`). Se aplica al «Actualizar edificio».
- Giro por pared: atributo de cara `cs2_f_rot` (cuartos de vuelta, panel Paredes y cuadro de componentes). Al armar cada pared se marca
  la capa `trot` de sus caras y `finish_bmesh` la suma al giro del material.
- Material por pared: ya existía (`cs2_f_mat`, materiales A a D). Se sumó `cs2.sel_wall_mat` (elegir las paredes que tienen un material).
- Ángulos que no son múltiplos de 90° agrandan la caja de cada pared en el atlas (baja un poco la nitidez) y en el horneado rozan más
  los bordes de las islas (hasta ~1 % de solape; `tex_rotate_bake` lo tolera hasta 2 %).

## Paredes curvas (v2.78)
- `curve_deg` (Lote y forma → «Unir curvas hasta», 25° por defecto, 0 = apagado). `read_shape` llama a `group_curved_walls`, que usa
  `curved_chains`: une paredes vecinas (borde vertical común, rectángulos de igual altura, mismos ajustes) cuyo giro es de 0,3° a `curve_deg`
  y que no cambian de sentido de giro. Cada pared de la cadena lleva `cfg["_chain"]` (id, posición, largo total).
- `shape_openings` desvía a `_chain_openings`: arma las aberturas en la pared desenrollada (largo total del arco, mirando al frente, con
  caché por cadena y LOD) y a cada faceta le da las que caen en ella (se corren hacia adentro si cruzan el borde; una ventana se angosta
  hasta 0,6 m si la faceta es corta; puertas y balcones solo en facetas que miran al frente, salvo «siempre»).
- Límite: una faceta tiene que medir al menos 0,6 m + 0,24 m para una ventana angosta y ~1,5 m para una normal. Los muros cortina y las
  cornisas siguen siendo por faceta. Falta: balcón corrido continuo que siga la curva, fachada por crujías, retícula estructural.
- `cs2.curve_select` (botón junto al ángulo) elige en la forma las paredes detectadas como curva.

## Herramientas de revisión (headless)
- `tools/hojas_componentes.py <carpeta> <window|door|balcony>`: hojas de contacto con todos los estilos incluidos.
- `tools/render_edificio.py <salida.png> [giro] [giro_pared_frontal]`: edificio con ladrillo y vista rápida para ver el giro.
- `component_audit` y `building_audit` (en las pruebas) detectan caras degeneradas, vértices sueltos y z-fighting en todos los estilos, techos, porches y estilos generales.
- `comp.py` es una copia vieja del bloque de componentes que ya vive dentro de `cs2_edificio_addon.py`: no se importa en ningún lado.

## Estado y pendientes
- 43/43 pruebas OK en v2.78 (`perf_big` con `CS2_PERF_SCALE` en equipos lentos).
- Hecho en v2.77: errores silenciosos al informe (quedan solo los de la barra de progreso/redibujo y `ValueError` esperados de `bm.faces.new`),
  giro de texturas, material y giro por pared, antepechos macizos sin z-fighting, pruebas nuevas (rotación, materiales, auditoría, recorrido).
- Visto en la revisión de componentes, sin tocar: las ventanas y puertas con forma (arco, círculo, óvalo...) se hunden en un nicho
  rectangular; un tubo que siga el contorno se vería mejor pero obliga a cortar la pared con polígonos en `_wall_cut`.
- Auditoría (2026-10-28) y mejoras propuestas, por orden recomendado:
  1. (hecho) Registrar en `_ERRLOG` los `try/except: pass` silenciosos.
  2. Vista previa de lote (celdas CS2) en el visor 3D.
  3. Deshacer por componente.
  4. Comparar variantes lado a lado; botón "Exportar todo".
  5. Partir `roof_build`, `insert_balcony`, `build_curtain` en pasos con pruebas.
  6. (hecho: `flow_full`) Pruebas de recorridos completos (crear → personalizar → regenerar → exportar).
- Decisiones pendientes de Leo: pasar los componentes del panel de rehacer a un diálogo con OK.
- Leo ya probó un asset en CS2 y funciona bien (2026-10-28).
