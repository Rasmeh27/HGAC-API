"""Tests del scoring de calidad de frame (nitidez/brillo/ranking)."""

from __future__ import annotations

from types import SimpleNamespace

from app.modules.lpr.frame_quality import (
    assess_quality,
    compute_brightness,
    compute_laplacian_sharpness,
    is_usable_frame,
    rank_frames,
)
from tests._lpr_service_fakes import make_blur_image, make_sharp_image


def test_sharp_image_has_more_variance_than_blur() -> None:
    sharp = compute_laplacian_sharpness(make_sharp_image(400, 300))
    blur = compute_laplacian_sharpness(make_blur_image(400, 300))
    assert sharp > blur
    assert blur == 0.0


def test_brightness_of_solid_image() -> None:
    assert compute_brightness(make_blur_image(100, 100, value=110)) == 110.0


def test_is_usable_frame_gating() -> None:
    # Nítido y brillo medio -> utilizable.
    assert is_usable_frame(
        500.0, 120.0, min_sharpness=80, min_brightness=30, max_brightness=235
    )
    # Borroso -> no.
    assert not is_usable_frame(
        10.0, 120.0, min_sharpness=80, min_brightness=30, max_brightness=235
    )
    # Quemado (brillo alto) -> no.
    assert not is_usable_frame(
        500.0, 240.0, min_sharpness=80, min_brightness=30, max_brightness=235
    )
    # Oscuro -> no.
    assert not is_usable_frame(
        500.0, 10.0, min_sharpness=80, min_brightness=30, max_brightness=235
    )


def test_assess_quality_flags_blur_as_unusable() -> None:
    q = assess_quality(
        make_blur_image(200, 200), min_sharpness=80, min_brightness=30, max_brightness=235
    )
    assert q.usable is False
    assert q.sharpness == 0.0


def test_rank_frames_orders_by_quality_descending() -> None:
    frames = [
        SimpleNamespace(quality=SimpleNamespace(score=0.2)),
        SimpleNamespace(quality=SimpleNamespace(score=0.9)),
        SimpleNamespace(quality=SimpleNamespace(score=0.5)),
    ]
    ranked = rank_frames(frames)
    assert [f.quality.score for f in ranked] == [0.9, 0.5, 0.2]
    # No muta la lista original.
    assert [f.quality.score for f in frames] == [0.2, 0.9, 0.5]
