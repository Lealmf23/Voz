"""Valida PyTorch/CUDA/Blackwell y FFmpeg en tu PC. Ejecutar con el venv del backend:
    python scripts/validate_hardware.py
No descarga modelos."""
import shutil
import sys

print("Python:", sys.version.split()[0])
print("FFmpeg:", shutil.which("ffmpeg") or "NO ENCONTRADO")
try:
    import torch
except ImportError:
    sys.exit("PyTorch no instalado. Instálalo con el wheel cu128 (ver docs/INSTALLATION.md).")
print("torch:", torch.__version__, "| CUDA runtime:", torch.version.cuda)
if not torch.cuda.is_available():
    sys.exit("CUDA NO disponible. Probable wheel CPU o antiguo (cu121/cu126). Reinstala cu128.")
name = torch.cuda.get_device_name(0)
free, total = torch.cuda.mem_get_info()
archs = torch.cuda.get_arch_list()
print(f"GPU: {name} | VRAM libre {free // 2**20} MB de {total // 2**20} MB")
print("Arquitecturas del wheel:", archs)
print("sm_120 (Blackwell):", "OK" if "sm_120" in archs else "FALTA -> usa un wheel cu128")
x = torch.randn(2048, 2048, device="cuda")
print("Prueba matmul en GPU:", "OK" if torch.isfinite(x @ x).all().item() else "FALLO")
