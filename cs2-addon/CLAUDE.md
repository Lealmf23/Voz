# CS2 Generador de Edificios (add-on de Blender)

Add-on de un solo archivo (`cs2_edificio_addon.py`, v2.82, ~16 900 líneas) que genera edificios low-poly
para Cities: Skylines 2 (y CS1) con LODs, UV, texturas y exportación FBX. Blender 4.2+.

## Cómo trabajar con Leo
- Responder SIEMPRE en español rioplatense (voseo). Leo vive en Buenos Aires.
- Avisarle de las decisiones que le tocan a él, con una recomendación.
- Es de uso personal y está hecho con ayuda de IA; el canal de YouTube "Leal" lo muestra.
- Toda la interfaz del add-on está en español.

## Pruebas (headless, sin abrir Blender)
    pip install bpy numpy pillow
    python3 tests/run_tests.py                 # todo, ~11 min, 50 casos
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
  cornisas siguen siendo por faceta. 
- `cs2.curve_select` (botón junto al ángulo) elige en la forma las paredes detectadas como curva.

## Crujías y balcón corrido (v2.79)
- Crujías = modos 6 a 9 de «Paños de color y crujías» (`pan_mode`, global y por pared): `_bay_plan` pinta columnas enteras (de piso a techo,
  sobre la planta baja si esta lleva otro material) que siguen cada columna de ventanas; los huecos de la columna comparten el paño, así
  las jambas también cambian. Salen en los tres LOD (en LOD1/LOD2 sin cortar aberturas, `_wall_cut` arma solo las tiras).
- Balcón corrido = estilo con `bal_run` (presets «Corrido continuo …»). `insert_balcony(..., plan_override=(planta, lados con baranda,
  lados contra la pared))` deja que `insert_balcony_run` arme una franja que sigue un recorrido y reutilice toda la baranda.
  Pared recta: una franja por piso desde el primer hasta el último balcón (`_emit_wall_runs`). Curva: la primera faceta de cada tramo
  de facetas que llevan balcón arma la franja de todo el tramo (`_emit_chain_runs`, con `facets` guardado en `cfg["_chain"]`);
  las demás no dibujan balcones propios.

## Retícula estructural y planta baja comercial (v2.80)
- Dos grupos de ajustes definidos por tabla (`NEW_GROUPS`: banderas `FLAG_GRID`/`FLAG_SHOP`, propiedades `gr_<k>_g`/`sh_<k>_g` del edificio y
  `gr_<k>`/`sh_<k>` de la pared, atributos de cara `cs2_gr_*`/`cs2_sh_*`). `group_cfg`/`group_on` resuelven lo que le toca a una pared;
  `_install_group_props`, `WALL_GROUPS`, `load_wall_ui`, el panel Paredes y los cuadros «Retícula»/«Planta comercial» salen de la misma tabla.
  Para sumar otro grupo parecido alcanza con una entrada nueva en `NEW_GROUPS`.
- Retícula (`_add_grid`): pilastras entre las columnas de aberturas (de piso a techo; sobre la vidriera si la pared la lleva) y losas en
  los frentes de piso (cada 1 a 3 pisos), cajas en relieve (`add_obox`) con el material elegido. LOD0 y LOD1.
- Planta comercial: `_shop_ops` (dentro de `shape_openings`) reemplaza las ventanas del piso bajo por tramos de vidrio liso (`MAT["gls"]`, sin
  estilo de ventana: `style_for` y `pieces_for` devuelven nada para `gls`) entre pilares, con el acceso dentro; `_add_shopfront` agrega pilares
  en los huecos entre tramos, la banda de cartel y toldos (`_add_awning`). Sobre una curva, `_chain_openings` usa un tramo por faceta.

## Editor de fachada por ranuras (v2.81)
- Panel «Aberturas de la pared (ranuras)» (`CS2_PT_ranuras`, pestaña Personalizar; hay que estar en «Elegir caras» con una pared activa): una
  grilla por pisos y columnas (V ventana, P puerta, B balcón, T vidriera, × borrada, * editada). Se elige una ranura y se edita su tipo, estilo,
  ancho, alto, antepecho y desplazamiento, «Vale para» ranura / columna / piso / pared; agregar, duplicar, borrar, restablecer, copiar y pegar.
- Modelo: cada abertura automática de `shape_openings` lleva una etiqueta `(piso, columna)` (la puerta es `(0, -1)`); `p["_slot_last"]` deja el
  reparto sin editar para la interfaz (`slot_layout`). Las ediciones viven en el objeto forma (`obj["cs2_slots"]`, JSON) con la clave geométrica
  de la pared en coordenadas del mundo (`_slot_key_of`; una curva usa inicio de la primera faceta y final de la última) y se asocian a la pared
  más cercana dentro de 0,3 m (`slot_edits_match`): sobreviven a regenerar y a mover poco la pared; si se mueve más, se pierden.
- Las ediciones llegan a `shape_openings` por `cfg["_slots"]` (`attach_slot_edits` en `read_shape`) y se aplican con `apply_slot_edits`/`_slot_build`:
  borrar, cambiar de tipo (con las medidas por defecto del tipo nuevo), medir, correr, estilo propio y ranuras agregadas (que no se pisen con otras).
  El estilo propio de una abertura viaja en `p["_ss"]` y lo lee `style_for(p, mat, cfg, op)`. En una curva las ediciones se aplican en la pared desenrollada
  (`_chain_virtual`) y después se reparten a las facetas.
- Los paneles viejos de «Fachada» (`CS2_PT_alcance`, `_win`, `_door`, `_rev`, `_bal`...) ya no se registran desde que existen los cuadros de
  componentes (`CS2_PT_comp`): no agregar interfaz nueva ahí. Lo de la grilla y «Elegir paredes por material» va en `CS2_PT_ranuras`/`CS2_PT_comp`.

## Editor web (carpeta `editor/`)
- Editor estilo Sims (planta, paredes curvas, aberturas con fantasma, catálogo con miniaturas, exportar) con Blender como motor: ver
  `editor/README.md`. `engine.py` arma la forma sin cortar por pisos (así las curvas se unen en cadenas), escribe atributos de cara por pared y
  `cs2_slots`, y devuelve malla + reparto de aberturas (`slot_layout`/`slot_effective`) por pared. Probar con `editor/tests/e2e.js`.
- `create_shape_object(..., pts=)` acepta una planta propia (lo usa el motor). Retranqueos: `make_levels`/`stepped_shape` arman una malla cerrada por tramos (`setbacks:[{from,ins}]`); las ediciones del tramo k de una pared van en `edge.sx[k]`.

## Herramientas de revisión (headless)
- `tools/hojas_componentes.py <carpeta> <window|door|balcony>`: hojas de contacto con todos los estilos incluidos.
- `tools/render_edificio.py <salida.png> [giro] [giro_pared_frontal]`: edificio con ladrillo y vista rápida para ver el giro.
- `component_audit` y `building_audit` (en las pruebas) detectan caras degeneradas, vértices sueltos y z-fighting en todos los estilos, techos, porches y estilos generales.
- `comp.py` es una copia vieja del bloque de componentes que ya vive dentro de `cs2_edificio_addon.py`: no se importa en ningún lado.

## Estado y pendientes
- 51/51 pruebas OK en v2.82 con bpy 4.5 y 50/50 también con bpy 5.2.1 (Python 3.13; Leo usa Blender 5.2.1). `perf_big` con `CS2_PERF_SCALE`.
- Tipos de fachada (v2.82): `FACADE_TYPES`/`FACADE_BASE` + `cs2.facade_type`, botones en `CS2_PT_comp` (Personalizar). Prueba `facade_types`.
- `tools/ui_dump.py`: vuelca como texto lo que muestra cada pestaña (auditoría de la interfaz sin abrir Blender).
- Cierre de Blender 5.2.1 reportado por Leo (2026-10-28): dentro del deshacer (`ED_undo_operator_repeat` → `memfile_undo_decode`)
  al cambiar valores en «Ajustar última operación» de un componente después de entrar/salir de Editar forma. No se reprodujo sin interfaz.
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
