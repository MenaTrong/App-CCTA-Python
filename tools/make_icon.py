"""Sinh icon ứng dụng từ chữ CCTA: python tools/make_icon.py

Tạo ccta_alert/assets/ccta.ico (16–256 px, dùng cho exe/shortcut và cửa sổ) và ccta.png (xem trước).
Mỗi kích thước được vẽ riêng để chữ vẫn rõ ở 16 px. Cần Pillow (chỉ khi sinh icon, app không cần).
"""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ACCENT = (14, 106, 138)      # theme.ACCENT #0e6a8a
ACCENT_DARK = (8, 74, 98)
RED = (232, 72, 58)
FONT = "C:/Windows/Fonts/seguibl.ttf"      # Segoe UI Black
OUT = Path(__file__).resolve().parents[1] / "ccta_alert" / "assets"
SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)


def fit_font(draw, text, max_w, max_h):
    size = int(max_h * 1.6)
    while size > 4:
        f = ImageFont.truetype(FONT, size)
        l, t, r, b = draw.textbbox((0, 0), text, font=f)
        if r - l <= max_w and b - t <= max_h:
            return f
        size -= 1
    return ImageFont.truetype(FONT, 4)


def render(n: int) -> Image.Image:
    ss = 4 if n < 64 else 2                      # vẽ lớn rồi thu nhỏ để mịn cạnh
    S = n * ss
    im = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    pad = 0 if n <= 24 else round(S * 0.04)
    rad = round(S * (0.18 if n <= 24 else 0.22))
    # nền: ô vuông bo góc, nửa dưới đậm hơn một chút
    d.rounded_rectangle((pad, pad, S - 1 - pad, S - 1 - pad), rad, fill=ACCENT)
    if n > 24:
        lower = Image.new("RGBA", (S, S), (0, 0, 0, 0))
        ImageDraw.Draw(lower).rounded_rectangle((pad, pad, S - 1 - pad, S - 1 - pad), rad, fill=ACCENT_DARK)
        mask = Image.new("L", (S, S), 0)
        ImageDraw.Draw(mask).rectangle((0, S * 0.62, S, S), fill=255)
        im.paste(lower, (0, 0), Image.composite(lower, Image.new("RGBA", (S, S)), mask).getchannel("A"))

    inner = S - 2 * pad
    text = "CCTA"
    small = n <= 24
    # chữ: chiếm gần hết chiều ngang; ở cỡ lớn nằm trên, chừa chỗ cho đường nhịp tim
    max_w = inner * (0.92 if small else 0.80)
    max_h = inner * (0.46 if small else 0.34)
    f = fit_font(d, text, max_w, max_h)
    l, t, r, b = d.textbbox((0, 0), text, font=f)
    cy = S * (0.50 if small else 0.40)
    d.text(((S - (r - l)) / 2 - l, cy - (b - t) / 2 - t), text, font=f, fill="white")

    if small:
        # gạch đỏ mảnh dưới chữ thay cho đường nhịp tim
        y = S * 0.82
        d.rectangle((S * 0.24, y - S * 0.045, S * 0.76, y + S * 0.045), fill=RED)
    else:
        # đường nhịp tim (ECG) màu đỏ ở phần dưới
        y0 = S * 0.74
        x0, x1 = pad + inner * 0.12, S - pad - inner * 0.12
        w = x1 - x0
        pts = [(0, 0), (.30, 0), (.38, -.06), (.44, 0), (.50, .07), (.57, -.20), (.64, .10), (.70, 0), (1, 0)]
        line = [(x0 + w * a, y0 + inner * h) for a, h in pts]
        width = max(ss * 2, round(S * 0.045))
        d.line(line, fill=RED, width=width, joint="curve")
        for p in (line[0], line[-1]):
            d.ellipse((p[0] - width / 2, p[1] - width / 2, p[0] + width / 2, p[1] + width / 2), fill=RED)
    return im.resize((n, n), Image.LANCZOS)


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    imgs = [render(n) for n in SIZES]
    imgs[-1].save(OUT / "ccta.ico", sizes=[(n, n) for n in SIZES], append_images=imgs[:-1])
    imgs[-1].save(OUT / "ccta.png")
    print("OK:", OUT / "ccta.ico")


if __name__ == "__main__":
    main()
