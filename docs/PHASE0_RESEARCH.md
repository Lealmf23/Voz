# Fase 0: Investigación y validación

Fecha: 2026-10-26. Fuentes: READMEs oficiales de cada proyecto y el foro de PyTorch.

**Límite de esta investigación:** el entorno de desarrollo es Linux sin GPU. Nada de lo de abajo se ha ejecutado en Windows 11 ni en la RTX 5070. Lo que sigue es lo que declaran las fuentes, no resultados medidos. La validación real se hace en la PC del usuario con `scripts/validate_hardware.py`, que está pendiente de escribir.

## Matriz de motores TTS / clonación

| Motor | es | en | pt-BR | Clonación | Control de emoción por audio | Licencia | Windows | Observaciones |
|---|---|---|---|---|---|---|---|---|
| Chatterbox Multilingual V3 (500M) | sí | sí | sí (`pt`, y un finetune pt-BR en el "Single Language Pack") | zero-shot desde audio de referencia | parcial (parámetro de exageración; no hay transferencia de prosodia frase a frase verificada) | MIT | no documentado. Probado en Debian con Python 3.11 | VRAM no documentada |
| IndexTTS-2.5 | sí | sí | **no listado** (zh, en, ja, es, ar) | zero-shot | sí: audio de referencia de emoción, `emo_alpha`, vector de 8 emociones y `duration_factor` | bilibili Model Use License (no es OSI; hay que revisarla) | sin soporte formal; DeepSpeed es difícil en Windows | La mejor candidata para expresividad, pero no sirve para pt-BR |
| Fun-CosyVoice3-0.5B | sí | sí | **no listado** (zh, en, ja, ko, de, es, fr, it, ru) | cross-lingual zero-shot | instrucciones de estilo (según su README) | Apache-2.0 | Linux como objetivo principal, con conda y Python 3.10 | Descartada para pt-BR |
| F5-TTS | pendiente | pendiente | pendiente (existen finetunes de la comunidad, sin verificar) | sí | no | pendiente | pendiente | No evaluada todavía |

## Decisión provisional

- **Motor principal: Chatterbox Multilingual.** Es el único candidato con pt-BR documentado, licencia MIT y clonación zero-shot. Se integra detrás de la interfaz `TTSEngine`.
- **Alternativa para en-US: IndexTTS-2.5.** Su control de emoción por audio de referencia es el más cercano a la transferencia de prosodia que pides. No cubre pt-BR.
- **Consecuencia:** la transferencia de expresividad quizá no sea igual en inglés y en portugués. Cada motor declara qué soporta, y la interfaz debe mostrar ajustes manuales cuando un motor no admita la transferencia directa.

## Compatibilidad con la RTX 5070 (Blackwell, sm_120)

- Según el foro de PyTorch, el soporte de Blackwell llega con PyTorch 2.7.0 y los wheels CUDA 12.8 (`cu128`), tanto en Windows como en Linux.
- Hay informes de errores por usar wheels antiguos (cu121 o cu126) y de fallos de Triton en Windows (`sm_120 is not defined`). Un informe aislado en Windows 11 con una RTX 5070 Ti usó un nightly.
- IndexTTS recomienda el CUDA Toolkit 12.8 o superior.
- **Riesgo abierto:** Chatterbox fija versiones de torch en su `pyproject.toml`, y esas versiones pueden chocar con `cu128`. Hay que comprobarlo instalando en la PC del usuario.
- Verificación al arrancar: `torch.cuda.get_arch_list()` debe contener `sm_120`.

## Pendiente de verificar

1. VRAM real de Chatterbox en 12 GB, medida en la máquina del usuario.
2. Si Chatterbox funciona en Windows con `cu128` y sin compilar nada.
3. Qué hace el finetune pt-BR: si sustituye al modelo multilingüe o se carga además de él.
4. Licencia y política de uso de los pesos de Chatterbox.
5. Faster-Whisper en Windows con CUDA 12.x: depende de CTranslate2 y de cuDNN.
6. Modelo de traducción local con es→en y es→pt-BR (candidatos: NLLB-200, MADLAD-400).
7. Calidad de pt-BR: solo se puede juzgar escuchándola.

## Fuentes

- https://github.com/resemble-ai/chatterbox
- https://github.com/index-tts/index-tts
- https://github.com/FunAudioLLM/CosyVoice
- https://discuss.pytorch.org/t/cuda-support-for-rtx-50x-on-windows/221309
- https://discuss.pytorch.org/t/pytorch-support-for-sm120/216099
