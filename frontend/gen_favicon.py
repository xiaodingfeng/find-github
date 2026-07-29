"""Generate a radar-themed favicon.ico for find-github.

Produces a multi-resolution ICO (16/32/48/64/128/256) plus a 512 PNG.
Design: rounded-square blue→plum gradient + radar rings + sweep beam + blips.
"""
import math
import os
from PIL import Image, ImageDraw, ImageFilter


def lerp(a, b, t):
    return a + (b - a) * t


def lerp_color(c1, c2, t):
    return tuple(int(lerp(c1[i], c2[i], t)) for i in range(3))


def make_gradient_bg(size):
    """Diagonal gradient from top-left blue to bottom-right plum."""
    top_left = (31, 111, 235)   # #1f6feb
    bot_right = (188, 140, 255) # #bc8cff
    img = Image.new('RGBA', (size, size), (0, 0, 0, 0))
    px = img.load()
    for y in range(size):
        for x in range(size):
            t = (x + y) / (2 * max(size - 1, 1))
            t = max(0.0, min(1.0, t))
            px[x, y] = (*lerp_color(top_left, bot_right, t), 255)
    return img


def round_corner_mask(size, radius):
    mask = Image.new('L', (size, size), 0)
    d = ImageDraw.Draw(mask)
    d.rounded_rectangle([0, 0, size - 1, size - 1], radius=radius, fill=255)
    return mask


def draw_radar(base, s):
    """Draw radar rings, sweep and blips onto a copy of base gradient."""
    img = base.copy()
    d = ImageDraw.Draw(img, 'RGBA')
    cx = cy = s / 2
    R = s * 0.40
    ring_color = (255, 255, 255, 90)
    ring_color_bright = (255, 255, 255, 150)

    # concentric rings
    for i, frac in enumerate([1.0, 0.72, 0.46, 0.22]):
        r = R * frac
        bbox = [cx - r, cy - r, cx + r, cy + r]
        d.ellipse(bbox, outline=ring_color, width=max(1, int(s / 128)))

    # cross hairs
    lw = max(1, int(s / 160))
    d.line([cx - R, cy, cx + R, cy], fill=(255, 255, 255, 40), width=lw)
    d.line([cx, cy - R, cx, cy + R], fill=(255, 255, 255, 40), width=lw)

    # sweep sector (45° wedge, fading)
    sweep = Image.new('RGBA', (s, s), (0, 0, 0, 0))
    sd = ImageDraw.Draw(sweep)
    start = -90  # start at top
    for deg in range(0, 55, 1):
        a = 120 - int(deg * 2)
        a = max(0, a)
        a0 = start + deg
        a1 = start + deg + 1.5
        sd.pieslice([cx - R, cy - R, cx + R, cy + R], a0, a1, fill=(121, 192, 255, a))
    img = Image.alpha_composite(img, sweep)
    d = ImageDraw.Draw(img, 'RGBA')

    # sweep leading line
    end_x = cx + R * math.cos(math.radians(-90 + 55))
    end_y = cy + R * math.sin(math.radians(-90 + 55))
    d.line([cx, cy, end_x, end_y], fill=(200, 225, 255, 220), width=max(1, int(s / 110)))

    # blips
    blips = [
        (cx + R * 0.55, cy - R * 0.30, (86, 211, 100, 255)),    # green
        (cx - R * 0.40, cy + R * 0.45, (240, 136, 62, 255)),    # coral
        (cx + R * 0.62, cy + R * 0.20, (210, 168, 255, 255)),   # plum
    ]
    br = max(2, int(s * 0.022))
    for bx, by, col in blips:
        d.ellipse([bx - br, by - br, bx + br, by + br], fill=col)

    # center dot
    cr = max(2, int(s * 0.028))
    d.ellipse([cx - cr, cy - cr, cx + cr, cy + cr], fill=(255, 255, 255, 230))
    return img


def render(size):
    bg = make_gradient_bg(size)
    mask = round_corner_mask(size, int(size * 0.22))
    rounded = Image.new('RGBA', (size, size), (0, 0, 0, 0))
    rounded.paste(bg, (0, 0), mask)
    radar = draw_radar(rounded, size)
    # soft outer glow
    glow = radar.filter(ImageFilter.GaussianBlur(size * 0.03))
    glow.putalpha(glow.split()[3].point(lambda v: min(v, 90)))
    out = Image.alpha_composite(glow, radar)
    return out


def main():
    os.makedirs('public', exist_ok=True)
    sizes = [16, 32, 48, 64, 128, 256]
    images = [render(s) for s in sizes]
    # ICO with multiple resolutions
    ico_path = 'public/favicon.ico'
    images[-1].save(ico_path, format='ICO', sizes=[(s, s) for s in sizes])
    # also save a 256 png + svg-ish png for crisp references
    images[-1].resize((512, 512), Image.LANCZOS).save('public/favicon-512.png')
    # apple touch icon (180)
    render(180).save('public/apple-touch-icon.png')
    print(f'wrote {ico_path} and PNG variants')


if __name__ == '__main__':
    main()
