"""Detect columns that probably hold personal or secret data (Phase 3, step 8).

Simple name rules, no AI: the same column name always gives the same answer.
When in doubt we mark it sensitive. A false alarm costs the admin one click;
a missed password or NIF could leak through the chat.
"""
from __future__ import annotations

import re
import unicodedata

SENSITIVE_WORDS = frozenset(
    {
        # secrets
        "password", "passwd", "pwd", "passphrase", "hash", "salt", "secret", "token", "apikey", "otp", "pin",
        "senha", "palavrapasse",
        # identity and tax numbers
        "nif", "nipc", "ssn", "taxid", "cc", "passport", "contribuinte",
        # bank and cards
        "iban", "nib", "swift", "bic", "card", "cardnumber", "creditcard", "cvv", "cvc", "cartao",
        # contacts
        "email", "phone", "telephone", "telefone", "telemovel", "mobile",
        # address
        "address", "morada", "endereco", "street", "postal", "zipcode", "postcode",
        # personal details
        "firstname", "lastname", "fullname", "surname", "birth", "birthday", "dob", "nascimento",
        "salary", "salario", "gender", "genero", "apelido", "nomecompleto",
        # technical
        "ip", "session",
    }
)

_CAMEL = re.compile(r"([a-z0-9])([A-Z])")
_SPLIT = re.compile(r"[^a-z0-9]+")


def strip_accents(name: str) -> str:
    """'cartão' -> 'cartao', 'endereço' -> 'endereco' (NFKD, then drop combining marks)."""
    decomposed = unicodedata.normalize("NFKD", name)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def name_tokens(name: str) -> list[str]:
    """'customerEmail' -> ['customer', 'email']; 'API_KEY' -> ['api', 'key'].

    Accents are removed first, so 'número_contribuinte' -> ['numero', 'contribuinte'].
    """
    spaced = _CAMEL.sub(r"\1 \2", strip_accents(name))
    return [t for t in _SPLIT.split(spaced.lower()) if t]


def is_sensitive_name(name: str) -> bool:
    tokens = name_tokens(name)
    # Single words and joined neighbours: "api_key" -> "apikey", "first_name" -> "firstname".
    candidates = set(tokens) | {a + b for a, b in zip(tokens, tokens[1:], strict=False)}
    return not candidates.isdisjoint(SENSITIVE_WORDS)
