@echo off
REM Abre el editor de edificios CS2. Usa Blender como motor (cierra esta ventana para apagarlo).
REM Si Blender está en otra carpeta, poné su ruta en la variable BLENDER antes de ejecutar.
if "%BLENDER%"=="" (
  for /d %%D in ("%ProgramFiles%\Blender Foundation\Blender *") do set "BLENDER=%%D\blender.exe"
)
if not exist "%BLENDER%" (
  echo No encontre Blender. Defini la variable BLENDER con la ruta de blender.exe y volve a ejecutar.
  pause & exit /b 1
)
"%BLENDER%" -b -P "%~dp0engine.py" -- --port 8765 --open
