from __future__ import annotations

from ..base import EngineInfo, EngineUnavailable

_CODES = {"es": "spa_Latn", "en-US": "eng_Latn", "pt-BR": "por_Latn"}


class NLLBTranslation:
    """Real, local. NLLB-200 (distilado 600M) vía transformers. NO ejecutado en el entorno de desarrollo.

    Licencia de los pesos: CC-BY-NC 4.0 (uso no comercial); revisar antes de monetizar el canal.
    NLLB traduce frase a frase: el contexto de los segmentos vecinos se ignora (limitación conocida).
    """
    info = EngineInfo("nllb-200-600m", "translation", "NLLB-200 distilled 600M", languages=list(_CODES),
                      license="CC-BY-NC-4.0 (pesos)", notes="Sin contexto entre frases; revisar a mano.")

    def __init__(self, model: str = "facebook/nllb-200-distilled-600M", device: str | None = None):
        try:
            import torch
            from transformers import AutoModelForSeq2SeqLM, AutoTokenizer
        except ImportError as e:
            raise EngineUnavailable("transformers/torch no instalados (requirements/ai.txt)") from e
        self._torch = torch
        self.device = device or ("cuda" if torch.cuda.is_available() else "cpu")
        self._tok = AutoTokenizer.from_pretrained(model)
        self._model = AutoModelForSeq2SeqLM.from_pretrained(model).to(self.device)

    def translate(self, text, source, target, context_before, context_after) -> str:
        if not text.strip():
            return ""
        self._tok.src_lang = _CODES[source]
        inputs = self._tok(text, return_tensors="pt").to(self.device)
        with self._torch.no_grad():
            out = self._model.generate(**inputs, forced_bos_token_id=self._tok.convert_tokens_to_ids(_CODES[target]),
                                       max_new_tokens=256, num_beams=4)
        return self._tok.batch_decode(out, skip_special_tokens=True)[0]

    def unload(self) -> None:
        self._model = None
        if self._torch.cuda.is_available():
            self._torch.cuda.empty_cache()
