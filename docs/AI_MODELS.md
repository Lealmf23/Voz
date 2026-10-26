# Modelos de IA

Detalle de la investigación en [PHASE0_RESEARCH.md](PHASE0_RESEARCH.md). **Ningún motor real se ha ejecutado todavía.**

| Función | Motor | Estado | Licencia | Limitaciones |
|---|---|---|---|---|
| Transcripción | Faster-Whisper | Adaptador escrito, sin ejecutar | MIT | Necesita CTranslate2/cuDNN compatibles en Windows (sin verificar). La confianza es `exp(avg_logprob)` del motor |
| Traducción | NLLB-200 distilled 600M | Adaptador escrito, sin ejecutar | Pesos CC-BY-NC 4.0 (no comercial) | Traduce frase a frase e ignora el contexto vecino. Revisa a mano |
| TTS y clonación | Chatterbox Multilingual | Adaptador escrito, sin ejecutar | MIT | Único candidato con pt-BR documentado. La API usada (`mtl_tts`, `generate`) debe verificarse con la versión instalada. Sin transferencia de prosodia |
| Alternativa expresiva | IndexTTS-2.5 | No integrado | bilibili Model Use License | Control de emoción por audio, pero sin portugués |
| Descartado | CosyVoice 3 | — | Apache-2.0 | Sin portugués |

La traducción y el glosario se aplican antes del TTS: los términos protegidos se sustituyen por marcadores y se restauran después.
