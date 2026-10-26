from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass
class Term:
    term: str
    en: str | None = None
    pt: str | None = None
    do_not_translate: bool = False
    priority: int = 0

    def target(self, lang: str) -> str:
        if self.do_not_translate:
            return self.term
        return (self.en if lang.startswith("en") else self.pt) or self.term


def protect(text: str, terms: list[Term], lang: str) -> tuple[str, dict[str, str]]:
    """Sustituye términos por marcadores antes de traducir; devuelve el mapa para restaurar."""
    mapping: dict[str, str] = {}
    for i, t in enumerate(sorted(terms, key=lambda t: (-t.priority, -len(t.term)))):
        pattern = re.compile(re.escape(t.term), re.IGNORECASE)
        if pattern.search(text):
            marker = f"⟦G{i}⟧"
            text = pattern.sub(marker, text)
            mapping[marker] = t.target(lang)
    return text, mapping


def restore(text: str, mapping: dict[str, str]) -> str:
    for marker, value in mapping.items():
        text = text.replace(marker, value)
    return text
