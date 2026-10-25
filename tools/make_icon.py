# -*- coding: utf-8 -*-
"""生成 DiskSentry 图标资源（纯 Pillow，一次性资产生成脚本，非运行时依赖）。

产出：
  assets/icon.ico        多尺寸 Windows 图标（exe 用，spec 引用）
  assets/icon.svg        矢量原稿（参考/文档）
  web/favicon.svg        网页矢量 favicon
  web/favicon.png        网页位图 favicon（32px，兼容旧浏览器）
  assets/icon-preview.png 256px 预览（仅人工检查用，可删）

重新生成：python tools/make_icon.py
"""
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]

# 设计坐标基于 64x64 视口，按倍率放大绘制后超采样缩回
S = 16  # 超采样倍数：64 * 16 = 1024

BG_TOP = (59, 130, 246)     # #3b82f6
BG_BOTTOM = (29, 78, 216)   # #1d4ed8
WHITE = (255, 255, 255, 255)
BLUE = (29, 78, 216, 255)

TREND = [(11, 31), (22, 21), (30, 27), (46, 11)]   # 折线
ARROW_TIP = (53.5, 6.5)
ARROW_BASE = [(49.5, 16.5), (43.5, 10.5)]           # 箭头三角底边两点


def sc(pts):
    return [(x * S, y * S) for x, y in pts]


def render(size: int) -> Image.Image:
    big = Image.new("RGBA", (64 * S, 64 * S), (0, 0, 0, 0))
    d = ImageDraw.Draw(big)

    # 渐变圆角背景：先画竖向渐变，再用圆角矩形蒙版裁剪
    grad = Image.new("RGBA", big.size)
    gd = ImageDraw.Draw(grad)
    for y in range(big.size[1]):
        t = y / (big.size[1] - 1)
        c = tuple(int(BG_TOP[i] + (BG_BOTTOM[i] - BG_TOP[i]) * t) for i in range(3)) + (255,)
        gd.line([(0, y), (big.size[0], y)], fill=c)
    mask = Image.new("L", big.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle([2 * S, 2 * S, 62 * S, 62 * S],
                                           radius=14 * S, fill=255)
    big.paste(grad, (0, 0), mask)

    # 趋势箭头（先画，让硬盘条压住它的下缘，层次更稳）
    d.line(sc(TREND), fill=WHITE, width=int(4.6 * S), joint="curve")
    d.polygon(sc([ARROW_TIP] + ARROW_BASE), fill=WHITE)

    # 硬盘条 + 指示灯
    d.rounded_rectangle([12 * S, 40 * S, 52 * S, 53 * S],
                        radius=4 * S, fill=WHITE)
    d.ellipse([44.5 * S, 44.5 * S, 49.5 * S, 49.5 * S], fill=BLUE)

    return big.resize((size, size), Image.LANCZOS)


SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">
  <defs>
    <linearGradient id="bg" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="#3b82f6"/>
      <stop offset="1" stop-color="#1d4ed8"/>
    </linearGradient>
  </defs>
  <rect x="2" y="2" width="60" height="60" rx="14" fill="url(#bg)"/>
  <polyline points="11,31 22,21 30,27 46,11" fill="none" stroke="#fff"
            stroke-width="4.6" stroke-linecap="round" stroke-linejoin="round"/>
  <polygon points="53.5,6.5 49.5,16.5 43.5,10.5" fill="#fff"/>
  <rect x="12" y="40" width="40" height="13" rx="4" fill="#fff"/>
  <circle cx="47" cy="47" r="2.5" fill="#1d4ed8"/>
</svg>
"""


def main():
    (ROOT / "assets").mkdir(exist_ok=True)
    (ROOT / "web").mkdir(exist_ok=True)

    sizes = [16, 32, 48, 64, 256]
    imgs = {s: render(s) for s in sizes}
    imgs[256].save(ROOT / "assets" / "icon.ico",
                   format="ICO", sizes=[(s, s) for s in sizes],
                   append_images=[imgs[s] for s in sizes if s != 256])
    imgs[32].save(ROOT / "web" / "favicon.png")
    imgs[256].save(ROOT / "assets" / "icon-preview.png")
    (ROOT / "assets" / "icon.svg").write_text(SVG, encoding="utf-8")
    (ROOT / "web" / "favicon.svg").write_text(SVG, encoding="utf-8")
    print("生成完成: assets/icon.ico, icon.svg, icon-preview.png, "
          "web/favicon.svg, web/favicon.png")


if __name__ == "__main__":
    main()
