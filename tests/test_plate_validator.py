"""Tests unitarios del validador de formato de placa."""

from app.modules.lpr.plate_validator import (
    DEFAULT_PLATE_FORMATS,
    KNOWN_PLATE_FORMATS,
    PlateFormat,
    PlateValidator,
    build_plate_formats,
)

validator = PlateValidator()  # por defecto: LETTER_6_DIGITS = ^[A-Z][0-9]{6}$


def test_complete_plate_matches_format() -> None:
    assert validator.matched_format("L460432") == "LETTER_6_DIGITS"
    assert validator.is_format_valid("L460432") is True


def test_missing_leading_letter_is_invalid() -> None:
    # El bug reportado: "460432" (sin la letra inicial) NO cumple formato.
    assert validator.matched_format("460432") is None
    assert validator.is_format_valid("460432") is False


def test_wrong_shapes_are_invalid() -> None:
    assert validator.is_format_valid("AB12345") is False  # dos letras
    assert validator.is_format_valid("L46043") is False  # 5 dígitos
    assert validator.is_format_valid("L4604321") is False  # 7 dígitos
    assert validator.is_format_valid("") is False
    assert validator.is_format_valid(None) is False


def test_expected_format_label() -> None:
    assert validator.expected_format == "LETTER_6_DIGITS"


def test_is_plausible_is_loose_but_requires_a_digit() -> None:
    assert validator.is_plausible("L460432") is True
    assert validator.is_plausible("ABCDEF") is False  # sin dígito
    assert validator.is_plausible("") is False


def test_custom_formats_are_honored() -> None:
    custom = PlateValidator(
        formats=(PlateFormat(name="TWO_FIVE", regex=r"^[A-Z]{2}[0-9]{5}$"),)
    )
    assert custom.matched_format("AB12345") == "TWO_FIVE"
    assert custom.is_format_valid("L460432") is False
    assert custom.expected_format == "TWO_FIVE"


# --- catálogo de formatos + builder desde config ---


def test_build_plate_formats_resolves_catalog_names() -> None:
    formats = build_plate_formats("LETTER_6_DIGITS,TWO_LETTERS_5_DIGITS")
    names = [fmt.name for fmt in formats]
    assert names == ["LETTER_6_DIGITS", "TWO_LETTERS_5_DIGITS"]
    validator = PlateValidator(formats=formats)
    assert validator.matched_format("L460432") == "LETTER_6_DIGITS"
    assert validator.matched_format("OF00105") == "TWO_LETTERS_5_DIGITS"
    assert validator.is_format_valid("460432") is False


def test_build_plate_formats_single_selection() -> None:
    formats = build_plate_formats("TWO_LETTERS_5_DIGITS")
    assert [fmt.name for fmt in formats] == ["TWO_LETTERS_5_DIGITS"]
    validator = PlateValidator(formats=formats)
    assert validator.is_format_valid("OF00105") is True
    assert validator.is_format_valid("L460432") is False  # ya no se acepta


def test_build_plate_formats_uses_override_for_unknown_name() -> None:
    formats = build_plate_formats("CUSTOM", regex_override=r"^[0-9]{4}$")
    assert formats[0].name == "CUSTOM"
    assert PlateValidator(formats=formats).is_format_valid("1234") is True


def test_build_plate_formats_falls_back_to_default_when_empty() -> None:
    assert build_plate_formats("") == DEFAULT_PLATE_FORMATS
    # nombre desconocido sin override -> se ignora -> default
    assert build_plate_formats("NOPE") == DEFAULT_PLATE_FORMATS


def test_known_catalog_contains_both_dr_shapes() -> None:
    assert KNOWN_PLATE_FORMATS["LETTER_6_DIGITS"] == r"^[A-Z][0-9]{6}$"
    assert KNOWN_PLATE_FORMATS["TWO_LETTERS_5_DIGITS"] == r"^[A-Z]{2}[0-9]{5}$"
