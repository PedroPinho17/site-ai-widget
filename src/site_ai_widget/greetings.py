"""Short replies for greetings and thanks. No database and no documents."""
from __future__ import annotations

import re

_HELLO = re.compile(
    r"^(?:ol[aá]|oi|hey|hi|hello|bom dia|boa tarde|boa noite|good morning|good afternoon|good evening)[!.?\s]*$",
    re.IGNORECASE,
)
_THANKS = re.compile(
    r"^(?:obrigad[oa]|thanks|thank you|thx)[!.?\s]*$",
    re.IGNORECASE,
)

_HELLO_REPLY = {
    "pt": "Olá! Em que posso ajudar?",
    "en": "Hello! How can I help?",
}
_THANKS_REPLY = {
    "pt": "De nada! Se precisar de mais alguma coisa, é só dizer.",
    "en": "You're welcome! Let me know if you need anything else.",
}


def small_talk_reply(question: str, lang: str | None) -> str | None:
    text = " ".join((question or "").strip().split())
    if not text or len(text) > 40:
        return None
    if lang and str(lang).lower().startswith("pt"):
        locale = "pt"
    elif lang and str(lang).lower().startswith("en"):
        locale = "en"
    elif re.search(r"[áàãâéêíóôõúç]|ol[aá]|oi|bom dia|boa |obrigad", text, re.I):
        locale = "pt"
    else:
        locale = "en"
    if _THANKS.match(text):
        return _THANKS_REPLY[locale]
    if _HELLO.match(text):
        return _HELLO_REPLY[locale]
    return None
