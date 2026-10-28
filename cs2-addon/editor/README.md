# Editor de edificios CS2 (estilo Sims)

Página web (`editor.html` + `editor.js`, Three.js incluido en `vendor/`) y un motor local (`engine.py`) que usa **Blender con el add-on**
para armar la malla real. Lo que ves en la vista 3D es la malla que genera el add-on, no una maqueta.

## Usarlo
- Windows: doble clic en `iniciar_editor.bat` (busca Blender en `Archivos de programa`; si no, definí la variable `BLENDER`).
- Linux/Mac: `BLENDER=/ruta/blender ./iniciar_editor.sh`. Sin Blender instalado: `pip install bpy numpy pillow` y `python3 engine.py --open`.
- Se abre http://127.0.0.1:8765. El motor corre en tu PC y no manda nada a internet.

## Cómo se usa (resumen)
Arriba hay una guía de 5 pasos (Planta · Paredes · Aberturas · Techo y color · Exportar). Al abrir, elegís una planta de partida o dibujás la tuya.
El botón **Planta** (D) edita la planta que ya tenés (nunca la borra; para empezar de cero está **Nuevo**). El panel derecho tiene pestañas:
Selección/Paredes, Edificio, Estilo, **Opciones** (todas las opciones de los cuadros de componentes del add-on: ventana, puerta, balcón, muro cortina,
paredes, retícula, planta comercial, techo, cornisas y porche, con buscador) y Retranqueo. Con una pared elegida, «Solo esta pared» cambia esas
opciones únicamente en ella. `engine.py` publica el esquema en `/schema` leyendo las propiedades del add-on, así que las opciones nuevas del
add-on aparecen solas.

## Qué se puede hacer
- Dibujar la planta con clics (se pega a la grilla; las líneas gruesas son las celdas de 8 m de CS2), mover esquinas, doble clic para agregar,
  clic derecho para borrar; plantas rápidas (caja, L, U, T, torre redonda, frente curvo).
- Curvar paredes (campo «Curvatura» o la manija amarilla en la planta): el motor las arma con facetas y el add-on las trata como curva.
- Elegir una pared: material A–D, giro de textura, estilos propios, vaciarla o restablecerla, copiar a todas.
- Colocar ventanas, puertas, balcones y vidrieras con fantasma, imán y catálogo con miniaturas (clic sobre una existente la reemplaza).
  Elegir una abertura: cambiar tipo, estilo, medidas, moverla arrastrando, borrarla (Supr).
- Colores de fachada, materiales B–D, molduras, techo y puertas (se ven en la vista 3D con el color real).
- Retranqueos: desde cualquier piso la planta se achica (retiro distinto por pared, varios escalones); las paredes del tramo superior se editan igual.
- Pisos, alto de piso, techo (todos los modelos del add-on), balcones y estilos generales; deshacer/rehacer (Ctrl+Z / Ctrl+Y); guardar y abrir `.json`.
- «Exportar a CS2»: Blender arma los tres LODs, las UV, las texturas y los FBX en la carpeta que elijas.

## Archivos
- `engine.py`: servidor (HTTP en hilo aparte, Blender en el hilo principal). `/build` receta → malla + reparto de aberturas por pared,
  `/export`, `/catalog`. Receta: `{verts, edges:[{bulge, mat, rot, ws, ds, bs, ov, add}], floors, floor_h, roof, props}`; `ov`/`add` son las
  ediciones de ranuras del add-on (`obj["cs2_slots"]`).
- `make_thumbs.py`: regenera `thumbs/` (con `LD_LIBRARY_PATH=/opt/egl EGL_PLATFORM=surfaceless LIBGL_ALWAYS_SOFTWARE=1` en equipos sin pantalla).
- `tests/e2e.js`: prueba completa con Playwright (dibujar, mover, curvar, colocar, borrar, deshacer, exportar).
  `NODE_PATH=<node_modules con playwright> node tests/e2e.js` con el motor andando.
