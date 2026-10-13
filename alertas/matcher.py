"""Detector de intención de compra.

Reglas (todas editables desde el panel):
- Si aparece una palabra de EXCLUSIÓN (ej. "vendo") no se alerta: el autor es vendedor.
- Hay señal de compra cuando aparece una frase de INTENCIÓN junto con un PRODUCTO,
  o cuando es una pregunta (con "?") que menciona un PRODUCTO.
- Si "exigir producto" está apagado, basta una frase de intención (modo amplio, más ruido).
"""
import re
import unicodedata
from dataclasses import dataclass, field


def normalize(text: str) -> str:
    t = unicodedata.normalize("NFKD", (text or "").lower())
    t = "".join(c for c in t if not unicodedata.combining(c))
    t = re.sub(r"[^a-z0-9?]+", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def _compile(terms):
    norm = sorted({normalize(t) for t in terms if normalize(t)}, key=len, reverse=True)
    if not norm:
        return None
    alt = "|".join(re.escape(t).replace(r"\ ", " ") for t in norm)
    return re.compile(r"(?<![a-z0-9])(" + alt + r")(?:s|es)?(?![a-z0-9])")


@dataclass
class Rules:
    intents: object = None
    products: object = None
    excludes: object = None


def build_rules(intents, products, excludes) -> Rules:
    return Rules(_compile(intents), _compile(products), _compile(excludes))


@dataclass
class Match:
    ok: bool = False
    score: int = 0
    intents: list = field(default_factory=list)
    products: list = field(default_factory=list)
    excluded: list = field(default_factory=list)
    is_question: bool = False
    reason: str = ""


def _found(rx, text):
    if rx is None:
        return []
    seen, out = set(), []
    for m in rx.finditer(text):
        w = m.group(1)
        if w not in seen:
            seen.add(w)
            out.append(w)
    return out


def evaluate(text: str, rules: Rules, require_product: bool = True, min_score: int = 3) -> Match:
    t = normalize(text)
    m = Match()
    if len(t) < 6:
        m.reason = "Texto muy corto"
        return m
    m.is_question = "?" in t
    m.excluded = _found(rules.excludes, t)
    m.intents = _found(rules.intents, t)
    m.products = _found(rules.products, t)
    m.score = min(len(m.intents), 2) * 3 + min(len(m.products), 2) * 2 + (1 if m.is_question else 0)

    if m.excluded:
        m.reason = "Excluida por: " + ", ".join(m.excluded)
        return m
    signal = (m.intents and (m.products or not require_product)) or (m.is_question and m.products)
    if not signal:
        if m.intents and require_product:
            m.reason = "Hay intención de compra pero no menciona ningún producto de tu lista"
        elif m.products:
            m.reason = "Menciona un producto pero no parece que alguien quiera comprar"
        else:
            m.reason = "No hay intención de compra ni productos"
        return m
    if m.score < min_score:
        m.reason = f"Puntaje {m.score} menor al mínimo ({min_score})"
        return m
    m.ok = True
    m.reason = "Posible comprador"
    return m
