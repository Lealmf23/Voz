# Pruebas del addon CS2

Se ejecutan con el módulo `bpy` (sin abrir Blender):

    pip install bpy numpy pillow
    python3 tests/run_tests.py                  # todo (unos 4-5 minutos)
    python3 tests/run_tests.py uv_bake ao_bake  # solo algunos casos
    python3 tests/run_tests.py --update-baselines   # guarda las métricas actuales como línea base

| Caso | Qué comprueba |
|---|---|
| lod_limits | Formas (caja, L, T, U) × pisos: LOD2 ≤ 500 triángulos, LOD1 < 50 % del LOD0, mallas válidas |
| uv_bake | UV únicas dentro de 0-1, compartición, aprovechamiento, nitidez (px/m) contra la línea base |
| uv_overlap | Las islas distintas no se pisan en el mapa (incluido el relleno del hueco de la azotea en L) |
| uv_align | Las caras que comparten isla caen en la misma hilada y en la misma junta vertical del ladrillo |
| wall_cuts | Las aberturas se cortan con el mínimo de caras: tiras laterales de alto completo y sin grilla |
| ao_bake | Sombras sin NaN, sin bloques negros, media razonable |
| assign_textures | Pasos 1-3 y quitar texturas |
| export_cs2 | FBX de los 3 LOD y texturas exportadas |
| manual_mesh | "Generar desde mi malla": LOD1 y LOD2 dentro de los límites |
| cs1 | Generación y exportación de CS1 |
| report_log | Registro de errores e informe |
| perf_big | Edificio de 40 m: tiempo de Regenerar UV y nitidez |

`baselines.json` guarda las métricas de referencia (px/m, islas, segundos). Una prueba falla si la métrica empeora más del 10 %
(25 % las islas, 50 % el tiempo). Si mejoras algo a propósito, actualiza la línea base.

| geom_golden | Geometría del LOD0 de 10 edificios (formas y techos) igual a golden.json |
| balcony_golden | Los 52 estilos de balcón en LOD0/LOD1 igual a balcony_golden.json |
| wall_golden | build_wall en LOD0/1/2 (niveles y modo CS1) igual a wall_golden.json |
| uv_golden | Métricas de UV de varios edificios dentro de tolerancia (golden_uv.json) |
| ui_draw | Todos los paneles de la barra lateral se dibujan sin errores (con y sin edificio, con y sin «Avanzado») |
| facade_holes | Con la limpieza activa la fachada no queda con huecos (cornisas, persianas, molduras) |
| icons | Todos los íconos del add-on existen en la versión de Blender |
| tex_rotate | Giro de texturas: por material (90°, 180°, ángulo libre) y por pared (cuartos de vuelta); UV dentro de 0-1 en los tres LODs, geometría igual y sin giro todo igual |
| tex_rotate_bake | Con el giro puesto, las UV únicas del horneado se arman sin islas superpuestas |
| wall_mats | Material A-D por pared en los tres LODs y «Elegir paredes por material» |
| comp_walls | El cuadro «Paredes» aplica material y giro solo a las paredes elegidas |
| component_audit | Todas las ventanas, puertas y balcones incluidos: sin caras degeneradas, sin vértices sueltos y sin z-fighting |
| building_audit | Edificios con cada techo, porche y estilo general incluidos: sin caras degeneradas ni z-fighting (LOD0 y LOD1) |
| curve_walls | Curvas hechas con facetas: detección por ángulo (círculo, octógono, caja, S) y ventanas/balcones repartidos por el arco; «Ver curvas detectadas» |
| bays | Crujías (modos 6 a 9 de paños): columnas enteras alternas / cada tres / bordes, de piso a techo, en LOD0 y LOD1, y sobre la planta baja con otro material |
| balcony_run | Balcón corrido: una sola losa por piso en pared recta y sobre toda una curva (balaustres, vidrio y macizo) |
| grid | Retícula estructural: pilastras y losas en relieve (modos 1 a 3, cada N pisos, por pared), sin defectos ni errores |
| shop | Planta baja comercial: vidriera con acceso, cartel, toldos, solo el frente, sobre una curva, sin defectos ni errores |
| comp_groups | Los cuadros «Retícula» y «Planta comercial» aplican a las paredes elegidas y solo a ellas |
| flow_full | Recorrido completo (crear, personalizar, regenerar, UV, sombras, texturas, exportar) sin errores en el informe |
| mip_bleed | Con mipmaps (textura achicada) un material no toma el color de su vecino, en el atlas ni en el horneado |
| quick_view | La vista rápida muestra la textura elegida sin UV ni horneado, se apaga y se actualiza con el botón |
| uv_zones | Prioridad de espacio por zona y zonas fijadas: lo fijado no se mueve al regenerar y no se pisa con otras islas |
| retexture | «Rehacer texturas» deja UV, sombras y texturas asignadas, y se puede repetir |
| tex_missing | Avisa de archivos de textura faltantes (panel, lista de control y mensajes) |
| ground_mat | Material distinto en la planta baja (1 o 2 pisos, en los tres LODs) |

## Notas
- `CS2_PERF_SCALE=2 python3 tests/run_tests.py perf_big` multiplica las líneas base de tiempo si el equipo es más lento que el de Leo.
- `CS2_UPDATE_GOLDEN=1` renueva los golden de balcones/paredes/geometría (solo si el cambio es a propósito: revisar el diff del JSON).
- Cada caso termina con `os._exit` apenas imprime el resultado: en algunos equipos Blender se cuelga al cerrar y el corredor esperaba el tope de 300 s.
- Si hay una biblioteca EGL instalada en el sistema, `bpy` se cuelga al redibujar sin ventana: dejarla fuera del `LD_LIBRARY_PATH` normal (ver CLAUDE.md).
