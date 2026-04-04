from __future__ import annotations

import re
from collections import Counter
from io import BytesIO

from PIL import Image, ImageColor


HEX_COLOR_RE = re.compile(r"^#([0-9a-fA-F]{3}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$")
RGB_COLOR_RE = re.compile(
    r"rgba?\(\s*(\d{1,3})[\s,]+(\d{1,3})[\s,]+(\d{1,3})(?:[\s,\/]+([0-9.]+))?\s*\)"
)
HSL_ALPHA_RE = re.compile(
    r"hsla?\(\s*[-0-9.]+\s*(?:deg)?[\s,]+[-0-9.]+%[\s,]+[-0-9.]+%(?:[\s,\/]+([0-9.]+))?\s*\)",
    re.IGNORECASE,
)
HEX_COLOR_TOKEN_RE = re.compile(r"#[0-9a-fA-F]{3,8}\b")
RGB_COLOR_TOKEN_RE = re.compile(r"rgba?\([^)]+\)", re.IGNORECASE)
HSL_COLOR_TOKEN_RE = re.compile(r"hsla?\([^)]+\)", re.IGNORECASE)
CSS_NAMED_COLORS = tuple(sorted({name.lower() for name in ImageColor.colormap}, key=len, reverse=True))
NAMED_COLOR_TOKEN_RE = re.compile(
    r"\b(" + "|".join(re.escape(name) for name in CSS_NAMED_COLORS) + r")\b",
    re.IGNORECASE,
)


def normalize_css_color(value: str | None) -> str | None:
    if not value:
        return None
    candidate = value.strip()
    lowered_candidate = candidate.lower()
    if lowered_candidate in {"transparent", "rgba(0, 0, 0, 0)", "rgba(0,0,0,0)"}:
        return None
    if HEX_COLOR_RE.match(candidate):
        if len(candidate) == 4:
            red, green, blue = candidate[1], candidate[2], candidate[3]
            return f"#{red}{red}{green}{green}{blue}{blue}".upper()
        if len(candidate) == 9 and candidate[-2:] == "00":
            return None
        if len(candidate) == 9:
            return candidate[:7].upper()
        return candidate.upper()
    match = RGB_COLOR_RE.match(candidate)
    if not match:
        alpha_match = HSL_ALPHA_RE.match(candidate)
        if alpha_match and alpha_match.group(1) is not None and float(alpha_match.group(1)) == 0:
            return None
        try:
            red, green, blue = ImageColor.getrgb(candidate)[:3]
        except ValueError:
            return None
        return f"#{red:02X}{green:02X}{blue:02X}"
    red, green, blue = (max(0, min(255, int(match.group(index)))) for index in range(1, 4))
    alpha = match.group(4)
    if alpha is not None and float(alpha) == 0:
        return None
    return f"#{red:02X}{green:02X}{blue:02X}"


def extract_color_tokens(value: str) -> list[str]:
    tokens: list[str] = []
    for pattern in (
        HEX_COLOR_TOKEN_RE,
        RGB_COLOR_TOKEN_RE,
        HSL_COLOR_TOKEN_RE,
        NAMED_COLOR_TOKEN_RE,
    ):
        for match in pattern.finditer(value):
            token = match.group(0)
            if token:
                tokens.append(token)
    return tokens


def extract_palette_from_image(image_bytes: bytes, limit: int = 8) -> list[tuple[str, int]]:
    with Image.open(BytesIO(image_bytes)) as image:
        image = image.convert("RGB")
        image.thumbnail((480, 480))
        quantized = image.quantize(colors=max(limit, 8), method=Image.MEDIANCUT)
        palette = quantized.getpalette() or []
        colors = quantized.getcolors() or []

    counter: Counter[str] = Counter()
    for count, palette_index in colors:
        start = palette_index * 3
        rgb = palette[start : start + 3]
        if len(rgb) != 3:
            continue
        hex_value = f"#{rgb[0]:02X}{rgb[1]:02X}{rgb[2]:02X}"
        counter[hex_value] += count

    return counter.most_common(limit)
