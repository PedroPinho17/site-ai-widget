"""Sensitive column names (no DB)."""
from __future__ import annotations

import pytest

from site_ai_widget.dbconn.sensitive import is_sensitive_name, name_tokens, strip_accents


def test_name_tokens_split_snake_and_camel_case():
    assert name_tokens("customerEmail") == ["customer", "email"]
    assert name_tokens("API_KEY") == ["api", "key"]


@pytest.mark.parametrize(
    "name",
    [
        "password",
        "password_hash",
        "API_KEY",
        "customerEmail",
        "e_mail",
        "nif",
        "phone_number",
        "date_of_birth",
        "codigo_postal",
        "client_ip",
        "first_name",
        "iban",
        "senha",
        "palavra_passe",
        "contribuinte",
        "cartao",
        "apelido",
        "nome_completo",
        # Accents are stripped before matching
        "cartão",
        "endereço",
        "número_contribuinte",
        "Morada",
        "morada_entrega",
        "endereco",
    ],
)
def test_sensitive_names(name):
    assert is_sensitive_name(name)


def test_accents_are_removed_before_splitting():
    assert strip_accents("cartão") == "cartao"
    assert name_tokens("endereço") == ["endereco"]
    assert name_tokens("número_contribuinte") == ["numero", "contribuinte"]
    assert name_tokens("CartãoCrédito") == ["cartao", "credito"]


@pytest.mark.parametrize(
    "name",
    [
        "id",
        "name",
        "price",
        "sku",
        "shipping_cost",
        "city",
        "unified_code",
        "created_at",
        "is_active",
        "category_id",
        "preço",
        "descrição",
    ],
)
def test_safe_names(name):
    assert not is_sensitive_name(name)
