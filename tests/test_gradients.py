# pyright: reportPrivateUsage=false
# pyright: reportUnusedParameter=false
# pyright: reportUnknownMemberType=false

import pytest

from qt_css_engine.gradients import (
    _fill_positions,
    _parse_direction,
    _parse_stop,
    _split_args,
    translate_gradients,
)

# ---------------------------------------------------------------------------
# _split_args
# ---------------------------------------------------------------------------


def test_split_args_simple() -> None:
    assert _split_args("a, b, c") == ["a", "b", "c"]


def test_split_args_nested_parens() -> None:
    # Commas inside rgba() must not split
    result = _split_args("rgba(0,0,0,0.5), red")
    assert result == ["rgba(0,0,0,0.5)", "red"]


def test_split_args_deeply_nested() -> None:
    result = _split_args("fn(a(b,c),d), e")
    assert result == ["fn(a(b,c),d)", "e"]


def test_split_args_single() -> None:
    assert _split_args("red") == ["red"]


def test_split_args_strips_whitespace() -> None:
    result = _split_args("  red  ,  blue  ")
    assert result == ["red", "blue"]


# ---------------------------------------------------------------------------
# _parse_stop
# ---------------------------------------------------------------------------


def test_parse_stop_color_only() -> None:
    pos, color = _parse_stop("red")
    assert pos is None
    assert color == "red"


def test_parse_stop_with_percentage() -> None:
    pos, color = _parse_stop("red 25%")
    assert pos == pytest.approx(0.25)
    assert color == "red"


def test_parse_stop_with_decimal() -> None:
    pos, color = _parse_stop("#ff0000 0.5")
    assert pos == pytest.approx(0.5)
    assert color == "#ff0000"


def test_parse_stop_zero_percent() -> None:
    pos, color = _parse_stop("blue 0%")
    assert pos == pytest.approx(0.0)
    assert color == "blue"


def test_parse_stop_hundred_percent() -> None:
    pos, color = _parse_stop("green 100%")
    assert pos == pytest.approx(1.0)
    assert color == "green"


def test_parse_stop_rgba() -> None:
    # rgba() with commas — stop regex must still find trailing position
    pos, color = _parse_stop("rgba(255,0,0,1) 50%")
    assert pos == pytest.approx(0.5)
    assert color == "rgba(255,0,0,1)"


# ---------------------------------------------------------------------------
# _fill_positions
# ---------------------------------------------------------------------------


def test_fill_positions_all_present() -> None:
    raw: list[tuple[float | None, str]] = [(0.0, "red"), (0.5, "green"), (1.0, "blue")]
    result = _fill_positions(raw)
    assert result == [(0.0, "red"), (0.5, "green"), (1.0, "blue")]


def test_fill_positions_all_missing() -> None:
    raw: list[tuple[float | None, str]] = [(None, "red"), (None, "green"), (None, "blue")]
    result = _fill_positions(raw)
    positions = [p for p, _ in result]
    assert positions[0] == pytest.approx(0.0)
    assert positions[-1] == pytest.approx(1.0)
    assert positions[1] == pytest.approx(0.5)


def test_fill_positions_endpoints_anchored() -> None:
    raw: list[tuple[float | None, str]] = [(None, "a"), (0.5, "b"), (None, "c")]
    result = _fill_positions(raw)
    assert result[0][0] == pytest.approx(0.0)
    assert result[1][0] == pytest.approx(0.5)
    assert result[2][0] == pytest.approx(1.0)


def test_fill_positions_two_stops() -> None:
    raw: list[tuple[float | None, str]] = [(None, "black"), (None, "white")]
    result = _fill_positions(raw)
    assert result[0][0] == pytest.approx(0.0)
    assert result[1][0] == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# _parse_direction
# ---------------------------------------------------------------------------


def test_parse_direction_named_to_right() -> None:
    assert _parse_direction("to right") == (0.0, 0.0, 1.0, 0.0)


def test_parse_direction_named_to_bottom() -> None:
    assert _parse_direction("to bottom") == (0.0, 0.0, 0.0, 1.0)


def test_parse_direction_named_to_top() -> None:
    assert _parse_direction("to top") == (0.0, 1.0, 0.0, 0.0)


def test_parse_direction_named_to_left() -> None:
    assert _parse_direction("to left") == (1.0, 0.0, 0.0, 0.0)


def test_parse_direction_named_diagonal() -> None:
    assert _parse_direction("to bottom right") == (0.0, 0.0, 1.0, 1.0)
    assert _parse_direction("to right bottom") == (0.0, 0.0, 1.0, 1.0)


def test_parse_direction_named_case_insensitive() -> None:
    assert _parse_direction("To Right") == (0.0, 0.0, 1.0, 0.0)


def test_parse_direction_angle_90deg() -> None:
    # 90deg = to right → x2=1, y2=0.5
    dir = _parse_direction("90deg")
    assert dir is not None
    _x1, _y1, x2, y2 = dir
    assert x2 == pytest.approx(1.0, abs=1e-4)
    assert y2 == pytest.approx(0.5, abs=1e-4)


def test_parse_direction_angle_0deg() -> None:
    # 0deg = to top → x2=0.5, y2=0
    dir = _parse_direction("0deg")
    assert dir is not None
    _x1, _y1, x2, y2 = dir
    assert x2 == pytest.approx(0.5, abs=1e-4)
    assert y2 == pytest.approx(0.0, abs=1e-4)


def test_parse_direction_angle_180deg() -> None:
    # 180deg = to bottom → x2=0.5, y2=1
    dir = _parse_direction("180deg")
    assert dir is not None
    _x1, _y1, x2, y2 = dir
    assert x2 == pytest.approx(0.5, abs=1e-4)
    assert y2 == pytest.approx(1.0, abs=1e-4)


def test_parse_direction_not_a_direction() -> None:
    assert _parse_direction("red") is None
    assert _parse_direction("50%") is None
    assert _parse_direction("") is None


# ---------------------------------------------------------------------------
# translate_gradients — linear-gradient
# ---------------------------------------------------------------------------


def test_linear_default_direction() -> None:
    # No direction → defaults to "to bottom" → y1=0, y2=1
    out = translate_gradients("linear-gradient(red, blue)")
    assert out.startswith("qlineargradient(")
    assert "y1:0" in out
    assert "y2:1" in out
    assert "stop:0 red" in out
    assert "stop:1 blue" in out


def test_linear_to_right() -> None:
    out = translate_gradients("linear-gradient(to right, red, blue)")
    assert "x1:0" in out
    assert "x2:1" in out
    assert "y1:0" in out
    assert "y2:0" in out


def test_linear_to_bottom_right() -> None:
    out = translate_gradients("linear-gradient(to bottom right, red, blue)")
    assert "x1:0" in out and "y1:0" in out
    assert "x2:1" in out and "y2:1" in out


def test_linear_angle_direction() -> None:
    out = translate_gradients("linear-gradient(90deg, red, blue)")
    assert out.startswith("qlineargradient(")
    assert "stop:0 red" in out
    assert "stop:1 blue" in out


def test_linear_stop_positions() -> None:
    out = translate_gradients("linear-gradient(red 0%, green 50%, blue 100%)")
    assert "stop:0 red" in out
    assert "stop:0.5 green" in out
    assert "stop:1 blue" in out


def test_linear_stop_positions_filled() -> None:
    # Middle stop has no position → interpolated to 0.5
    out = translate_gradients("linear-gradient(black, gray, white)")
    assert "stop:0 black" in out
    assert "stop:0.5 gray" in out
    assert "stop:1 white" in out


def test_linear_rgba_stops() -> None:
    out = translate_gradients("linear-gradient(rgba(255,0,0,1), rgba(0,0,255,0.5))")
    assert out.startswith("qlineargradient(")
    assert "rgba(255,0,0,1)" in out
    assert "rgba(0,0,255,0.5)" in out


def test_linear_too_few_stops_passthrough() -> None:
    val = "linear-gradient(to right, red)"
    assert translate_gradients(val) == val


def test_linear_no_args_passthrough() -> None:
    val = "linear-gradient()"
    assert translate_gradients(val) == val


def test_linear_unmatched_paren_passthrough() -> None:
    val = "linear-gradient(red, blue"
    assert translate_gradients(val) == val


def test_non_gradient_passthrough() -> None:
    val = "steelblue"
    assert translate_gradients(val) == val


# ---------------------------------------------------------------------------
# translate_gradients — radial-gradient
# ---------------------------------------------------------------------------


def test_radial_defaults() -> None:
    out = translate_gradients("radial-gradient(red, blue)")
    assert out.startswith("qradialgradient(")
    assert "cx:0.5" in out
    assert "cy:0.5" in out
    assert "radius:0.5" in out
    assert "stop:0 red" in out
    assert "stop:1 blue" in out


def test_radial_at_position() -> None:
    out = translate_gradients("radial-gradient(circle at 25% 75%, red, blue)")
    assert "cx:0.25" in out
    assert "cy:0.75" in out


def test_radial_explicit_radius_percent() -> None:
    out = translate_gradients("radial-gradient(circle 30% at 50% 50%, red, blue)")
    assert "radius:0.3" in out


def test_radial_shape_keyword_ignored() -> None:
    # ellipse keyword accepted, no crash
    out = translate_gradients("radial-gradient(ellipse, red, blue)")
    assert out.startswith("qradialgradient(")


def test_radial_size_keyword_ignored() -> None:
    out = translate_gradients("radial-gradient(closest-side, red, blue)")
    assert out.startswith("qradialgradient(")


def test_radial_focal_equals_center() -> None:
    out = translate_gradients("radial-gradient(circle at 30% 70%, red, blue)")
    # fx/fy must equal cx/cy
    assert "fx:0.3" in out
    assert "fy:0.7" in out


def test_radial_too_few_stops_passthrough() -> None:
    val = "radial-gradient(circle at 50% 50%, red)"
    assert translate_gradients(val) == val


# ---------------------------------------------------------------------------
# translate_gradients — conic-gradient
# ---------------------------------------------------------------------------


def test_conic_defaults() -> None:
    out = translate_gradients("conic-gradient(red, blue)")
    assert out.startswith("qconicalgradient(")
    assert "cx:0.5" in out
    assert "cy:0.5" in out
    assert "angle:0" in out
    assert "stop:0 red" in out
    assert "stop:1 blue" in out


def test_conic_from_angle() -> None:
    out = translate_gradients("conic-gradient(from 90deg, red, blue)")
    assert "angle:90" in out


def test_conic_at_position() -> None:
    out = translate_gradients("conic-gradient(at 25% 75%, red, blue)")
    assert "cx:0.25" in out
    assert "cy:0.75" in out


def test_conic_from_and_at() -> None:
    out = translate_gradients("conic-gradient(from 45deg at 10% 90%, red, blue)")
    assert "angle:45" in out
    assert "cx:0.1" in out
    assert "cy:0.9" in out


def test_conic_negative_angle() -> None:
    out = translate_gradients("conic-gradient(from -90deg, red, blue)")
    assert "angle:-90" in out


def test_conic_too_few_stops_passthrough() -> None:
    val = "conic-gradient(from 0deg, red)"
    assert translate_gradients(val) == val


# ---------------------------------------------------------------------------
# translate_gradients — multiple gradients in one value
# ---------------------------------------------------------------------------


def test_multiple_gradients_in_value() -> None:
    val = "linear-gradient(red, blue), radial-gradient(green, yellow)"
    out = translate_gradients(val)
    assert "qlineargradient(" in out
    assert "qradialgradient(" in out


def test_gradient_in_background_shorthand() -> None:
    # Non-gradient prefix must survive
    val = "url(img.png), linear-gradient(red, blue)"
    out = translate_gradients(val)
    assert out.startswith("url(img.png)")
    assert "qlineargradient(" in out
