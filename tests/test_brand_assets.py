"""The brand icon shipped for Home Assistant.

Since HA 2026.3 a custom integration serves its own `brand/` images, taking
priority over the CDN. Before this, `brand/icon.png` was the manufacturer
wordmark at 277x209 — not square, so invalid as an icon — and nothing caught it:
a bad icon fails silently in the UI, never in CI.

Requirements are from the Home Assistant brands spec:
https://github.com/home-assistant/brands
"""

from __future__ import annotations

import pathlib

import pytest

Image = pytest.importorskip("PIL.Image", reason="Pillow not installed")

BRAND = pathlib.Path(__file__).resolve().parents[1] / (
    "custom_components/tuya_ev_charger/brand"
)

# (filename, exact required side in px). 1x and its hDPI double.
ICONS = [("icon.png", 256), ("icon@2x.png", 512)]


@pytest.mark.parametrize(("name", "side"), ICONS)
def test_icon_is_png_of_the_exact_required_size(name, side):
    path = BRAND / name
    assert path.is_file(), f"{name} is missing"
    with Image.open(path) as im:
        assert im.format == "PNG", f"{name} must be PNG, is {im.format}"
        # Exact, not "at least": the spec pins 256 and 512, and HA scales from
        # them assuming those dimensions.
        assert im.size == (side, side), f"{name} must be {side}x{side}, is {im.size}"


@pytest.mark.parametrize(("name", "side"), ICONS)
def test_icon_has_real_transparency(name, side):
    """RGBA with actually-transparent pixels.

    A re-flattening to JPEG or onto a solid background — the exact failure of the
    source file this replaced — would trip this.
    """
    with Image.open(BRAND / name) as im:
        assert im.mode == "RGBA", f"{name} must be RGBA, is {im.mode}"
        alpha = im.getchannel("A")
        assert alpha.getextrema()[0] == 0, f"{name} has no transparent pixels"


@pytest.mark.parametrize(("name", "side"), ICONS)
def test_icon_is_trimmed(name, side):
    """No wasted padding: the subject must reach the canvas edge.

    The spec asks for the minimum empty space around the subject. The art is
    slightly wider than tall, so it is squared and centred — meaning it fills one
    axis edge-to-edge and carries a small symmetric margin on the other. So:
    at least one axis spans fully, and any margin on the other stays small.
    """
    with Image.open(BRAND / name) as im:
        bbox = im.getchannel("A").getbbox()  # (left, upper, right, lower)
    assert bbox is not None, f"{name} is fully transparent"
    left, upper, right, lower = bbox
    h_span = right - left
    v_span = lower - upper

    fills_horizontally = left == 0 and right == side
    fills_vertically = upper == 0 and lower == side
    assert fills_horizontally or fills_vertically, (
        f"{name} is not trimmed: bbox {bbox} touches no full edge"
    )
    # The centring margin on the non-filled axis must be minor, not a border.
    assert h_span >= side * 0.9, f"{name} has too much horizontal padding"
    assert v_span >= side * 0.9, f"{name} has too much vertical padding"


@pytest.mark.parametrize(("name", "side"), ICONS)
def test_icon_has_substantial_content(name, side):
    """Guards against an empty or all-opaque render.

    A line-art icon is mostly transparent; a solid block or a blank canvas both
    fall outside a plausible opaque fraction.
    """
    with Image.open(BRAND / name) as im:
        alpha = im.getchannel("A")
    # getcolors() returns (count, value) pairs, value being the alpha level.
    opaque = sum(count for count, value in alpha.getcolors(maxcolors=256) if value > 128)
    fraction = opaque / (side * side)
    assert 0.05 < fraction < 0.75, f"{name} opaque fraction {fraction:.2%} is implausible"
