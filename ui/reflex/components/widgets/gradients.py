"""Tiny gradient-texture helpers for the facelift depth model.

Kivy has no CSS box-shadow or linear-gradient fill, so "raised" and "recessed"
surfaces are faked with cheap textures stretched over a ``RoundedRectangle``:

* A **2-stop vertical gradient** is a 1x2 RGBA texture (bottom texel + top
  texel) with linear filtering -- the GPU interpolates it to any size, so one
  tiny texture paints a button of any dimension.
* A **top inner-shadow** (for sunken wells) is a 1xN texture that is dark at the
  top and fades to transparent within the first ~quarter, pinned to the well's
  top edge.

Textures are cached by their (rounded) color stops so a theme switch reuses
them and we never rebuild the same gradient twice. The cache is unbounded by
design: the number of distinct stops is tiny (a handful per theme).
"""

from kivy.graphics.texture import Texture

_CACHE: dict = {}


def _rgba_bytes(rgba) -> bytes:
    return bytes(int(max(0.0, min(1.0, c)) * 255) for c in rgba)


def _key(tag, *colors):
    return (tag,) + tuple(round(c, 4) for col in colors for c in col)


def vgrad_texture(bottom_rgba, top_rgba) -> Texture:
    """A vertical 2-stop gradient as a 1x2 RGBA texture.

    Texture row 0 is the bottom of a Kivy ``Rectangle`` (tex-coord origin is
    bottom-left), so ``bottom_rgba`` is the first texel and ``top_rgba`` the
    second. Stretched over a rect it reads light-top/dark-bottom (raised) or
    whatever stops you pass.
    """
    k = _key("v", bottom_rgba, top_rgba)
    tex = _CACHE.get(k)
    if tex is not None:
        return tex
    tex = Texture.create(size=(1, 2), colorfmt="rgba")
    tex.blit_buffer(
        _rgba_bytes(bottom_rgba) + _rgba_bytes(top_rgba),
        colorfmt="rgba", bufferfmt="ubyte",
    )
    tex.wrap = "clamp_to_edge"
    tex.mag_filter = "linear"
    tex.min_filter = "linear"
    _CACHE[k] = tex
    return tex


def radial_glow_texture(size: int = 48, gamma: float = 1.15) -> Texture:
    """A soft round glow: a white NxN RGBA texture, opaque at the centre fading
    to transparent at the edge. Draw it tinted (Color = led_color + low alpha)
    behind an LED dot for a halo. Single shared texture -- tint per use."""
    k = ("glow", size, round(gamma, 3))
    tex = _CACHE.get(k)
    if tex is not None:
        return tex
    c = (size - 1) / 2.0
    buf = bytearray()
    for y in range(size):
        for x in range(size):
            dx, dy = (x - c) / c, (y - c) / c
            d = (dx * dx + dy * dy) ** 0.5
            a = max(0.0, 1.0 - d)
            buf += bytes((255, 255, 255, int((a ** gamma) * 255)))
    tex = Texture.create(size=(size, size), colorfmt="rgba")
    tex.blit_buffer(bytes(buf), colorfmt="rgba", bufferfmt="ubyte")
    tex.wrap = "clamp_to_edge"
    tex.mag_filter = "linear"
    tex.min_filter = "linear"
    _CACHE[k] = tex
    return tex


def side_shadow_texture(shadow_rgba, steps: int = 16) -> Texture:
    """A horizontal inner shadow: transparent at the left, ramping to
    ``shadow_rgba`` at the right edge. Painted as a narrow vertical strip along
    a recessed spine's right edge so the tab column reads as a sunken channel.
    """
    a = shadow_rgba[3] if len(shadow_rgba) > 3 else 1.0
    rgb = list(shadow_rgba[:3])
    k = _key("ss", (rgb[0], rgb[1], rgb[2], a), (steps, 0.0, 0.0))
    tex = _CACHE.get(k)
    if tex is not None:
        return tex
    buf = bytearray()
    for col in range(steps):
        # 0 at the left edge, 1 at the right edge (quadratic so it hugs the edge).
        t = col / max(steps - 1, 1)
        buf += _rgba_bytes((rgb[0], rgb[1], rgb[2], a * t * t))
    tex = Texture.create(size=(steps, 1), colorfmt="rgba")
    tex.blit_buffer(bytes(buf), colorfmt="rgba", bufferfmt="ubyte")
    tex.wrap = "clamp_to_edge"
    tex.mag_filter = "linear"
    tex.min_filter = "linear"
    _CACHE[k] = tex
    return tex


def top_shadow_texture(shadow_rgba, steps: int = 16, falloff: float = 0.32) -> Texture:
    """A top-down inner shadow: ``shadow_rgba`` at the top edge fading to fully
    transparent by ``falloff`` of the height, transparent below.

    Used for recessed wells -- a short dark band tucked under the top lip sells
    the "sunken" read without a real drop shadow.
    """
    a = shadow_rgba[3] if len(shadow_rgba) > 3 else 1.0
    rgb = list(shadow_rgba[:3])
    k = _key("ts", (rgb[0], rgb[1], rgb[2], a), (steps, falloff, 0.0))
    tex = _CACHE.get(k)
    if tex is not None:
        return tex
    buf = bytearray()
    # Row 0 = bottom of the rect, row steps-1 = top. Shadow lives at the top.
    for row in range(steps):
        # 0.0 at the very top edge, 1.0 at the bottom.
        depth = (steps - 1 - row) / max(steps - 1, 1)
        if depth >= falloff:
            alpha = 0.0
        else:
            alpha = a * (1.0 - depth / falloff)
        buf += _rgba_bytes((rgb[0], rgb[1], rgb[2], alpha))
    tex = Texture.create(size=(1, steps), colorfmt="rgba")
    tex.blit_buffer(bytes(buf), colorfmt="rgba", bufferfmt="ubyte")
    tex.wrap = "clamp_to_edge"
    tex.mag_filter = "linear"
    tex.min_filter = "linear"
    _CACHE[k] = tex
    return tex
