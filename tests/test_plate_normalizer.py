"""Tests unitarios del normalizador de placas."""

from app.modules.lpr.plate_normalizer import PlateNormalizer

normalizer = PlateNormalizer()


def test_uppercases_and_removes_spaces() -> None:
    assert normalizer.normalize("a 123 456") == "A123456"


def test_removes_hyphens_and_punctuation() -> None:
    assert normalizer.normalize("A-123.456") == "A123456"


def test_trims_surrounding_whitespace() -> None:
    assert normalizer.normalize("  abc123  ") == "ABC123"


def test_keeps_only_alphanumeric() -> None:
    assert normalizer.normalize("P!B@1#2$3") == "PB123"


def test_empty_string_returns_empty() -> None:
    assert normalizer.normalize("") == ""


def test_none_returns_empty() -> None:
    assert normalizer.normalize(None) == ""


def test_already_normalized_is_idempotent() -> None:
    assert normalizer.normalize("A123456") == "A123456"


def test_only_punctuation_returns_empty() -> None:
    assert normalizer.normalize("---") == ""
    assert normalizer.normalize("!!! ...") == ""


def test_strips_accented_and_non_ascii_characters() -> None:
    # Conservador: caracteres no [A-Z0-9] (incluidos acentos) se eliminan.
    assert normalizer.normalize("ABÇ-12") == "AB12"
    assert normalizer.normalize("ñ123ñ") == "123"
