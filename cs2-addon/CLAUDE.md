# CS2 Generador de Edificios (add-on de Blender)

Add-on de un solo archivo (`cs2_edificio_addon.py`, v2.76, ~15 000 líneas) que genera edificios low-poly
para Cities: Skylines 2 (y CS1) con LODs, UV, texturas y exportación FBX. Blender 4.2+.

## Cómo trabajar con Leo
- Responder SIEMPRE en español rioplatense (voseo). Leo vive en Buenos Aires.
- Avisarle de las decisiones que le tocan a él, con una recomendación.
- Es de uso personal y está hecho con ayuda de IA; el canal de YouTube "Leal" lo muestra.
- Toda la interfaz del add-on está en español.

## Pruebas (headless, sin abrir Blender)
    pip install bpy numpy pillow
    python3 tests/run_tests.py                 # todo, ~5 min, 35 casos
    python3 tests/run_tests.py roof_join scope_sync
    python3 tests/run_tests.py --update-baselines   # solo si la mejora es a propósito
- Los tests importan `cs2_edificio_addon` desde la carpeta raíz. Ver `tests/README.md`.
- `mip_bleed` a veces falla en la corrida completa y pasa solo.
- Antes de dar algo por arreglado: agregar un test que FALLE con el código viejo.

## Mapa del código
- `roof_build` (~línea 7000+): techos; rectángulos de `roof_rects`, vecinos con `_roof_neighbors`,
  voladizos `ov_m/ov_p`, relleno de hastiales (`ends`).
- Alcance "Todo el edificio / Caras elegidas": `_scope_update`, `_depsgraph_handler`, `_comp_invoke_state`.
- Paneles `CS2_PT_*` (25), operadores `CS2_OT_*` (~59). Exportación: `CS2_PT_export`, `CS2_PT_export_go`.
- Registro de errores: `_ERRLOG` + "Copiar informe".

## Estado y pendientes
- 35/35 pruebas OK en v2.76.
- Auditoría (2026-10-28) y mejoras propuestas, por orden recomendado:
  1. Registrar en `_ERRLOG` los ~58 `try/except: pass` silenciosos.
  2. Vista previa de lote (celdas CS2) en el visor 3D.
  3. Deshacer por componente.
  4. Comparar variantes lado a lado; botón "Exportar todo".
  5. Partir `roof_build`, `insert_balcony`, `build_curtain` en pasos con pruebas.
  6. Pruebas de recorridos completos (crear → personalizar → regenerar → exportar).
- Decisiones pendientes de Leo: probar un asset en el juego real; pasar los componentes del panel
  de rehacer a un diálogo con OK.
- Nunca se probó un asset en el juego: los límites de lote/triángulos/texturas son reglas, no verificación real.
