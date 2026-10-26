# Modelos de IA

Detalle de la investigación en [PHASE0_RESEARCH.md](PHASE0_RESEARCH.md). **Ningún motor real se ha ejecutado todavía.**

| Función | Motor | Estado | Licencia | Limitaciones |
|---|---|---|---|---|
| Transcripción | Faster-Whisper | Adaptador escrito, sin ejecutar | MIT | Necesita CTranslate2/cuDNN compatibles en Windows (sin verificar). La confianza es `exp(avg_logprob)` del motor |
| Traducción | NLLB-200 distilled 600M | Adaptador escrito, sin ejecutar | Pesos CC-BY-NC 4.0 (no comercial) | Traduce frase a frase e ignora el contexto vecino. Revisa a mano |
| Traducción (opcional, en línea) | Claude `claude-sonnet-5-5` por API | Adaptador escrito; probado solo con un cliente simulado, **nunca con la API real** | Servicio de pago de Anthropic | Envía **texto** a internet (nunca audio). Desactivado por defecto; exige consentimiento explícito y clave API (guardada en el almacén de credenciales del sistema). Usa contexto de segmentos vecinos y respeta el glosario. Modelo editable en `external_services.json`. Tiene coste por uso. Un rechazo de seguridad se muestra como error del segmento; no se activaron los fallbacks del lado del servidor |
| TTS y clonación | Chatterbox Multilingual | Adaptador escrito, sin ejecutar | MIT | Único candidato con pt-BR documentado. La API usada (`mtl_tts`, `generate`) debe verificarse con la versión instalada. Sin transferencia de prosodia |
| TTS y clonación (alternativa) | Qwen3-TTS 1.7B Base | Adaptador escrito, sin ejecutar (probado con un modelo simulado) | Apache-2.0 | API tomada del README; el modelo Base no documenta control de estilo. Usa la transcripción de la muestra si la das. No confirmado que su portugués sea pt-BR. Compáralo con `scripts/compare_tts.py` |
| Alternativa expresiva | IndexTTS-2.5 | No integrado | bilibili Model Use License | Control de emoción por audio, pero sin portugués |
| Descartado | CosyVoice 3 | — | Apache-2.0 | Sin portugués |

La traducción y el glosario se aplican antes del TTS: los términos protegidos se sustituyen por marcadores y se restauran después.
