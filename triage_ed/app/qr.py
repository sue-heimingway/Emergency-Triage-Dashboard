"""离线二维码生成。

用 segno（纯 Python，已 vendor 进 vendor/ 目录，BSD-3-Clause），
自己渲染成 SVG，不依赖任何在线服务 —— 演示现场断网也能扫码。
"""
from __future__ import annotations

import sys
from pathlib import Path

_VENDOR = Path(__file__).resolve().parent.parent / "vendor"
if str(_VENDOR) not in sys.path:
    sys.path.insert(0, str(_VENDOR))

try:  # pragma: no cover - 环境相关
    import segno  # type: ignore

    AVAILABLE = True
except Exception:  # pragma: no cover
    segno = None  # type: ignore
    AVAILABLE = False


def svg(text: str, scale: int = 9, border: int = 2) -> str:
    """返回一段可直接内联的 SVG；不可用时返回空串，前端会自动降级成纯链接。"""
    if not AVAILABLE or not text:
        return ""
    qr = segno.make(text, error="m", boost_error=False)
    matrix = list(qr.matrix)
    size = len(matrix)
    total = (size + border * 2) * scale
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{total}" height="{total}" '
        f'viewBox="0 0 {total} {total}" role="img" aria-label="候诊二维码">',
        f'<rect width="{total}" height="{total}" fill="#ffffff"/>',
        f'<path fill="#0f172a" d="',
    ]
    # 用 path 一次性画完所有黑块，比几千个 rect 小得多
    for y, row in enumerate(matrix):
        x = 0
        while x < size:
            if row[x]:
                run = 1
                while x + run < size and row[x + run]:
                    run += 1
                parts.append(
                    f"M{(x + border) * scale} {(y + border) * scale}"
                    f"h{run * scale}v{scale}h{-run * scale}z"
                )
                x += run
            else:
                x += 1
    parts.append('"/>')
    parts.append("</svg>")
    return "".join(parts)


def data_uri(text: str, scale: int = 9) -> str:
    import urllib.parse

    raw = svg(text, scale=scale)
    if not raw:
        return ""
    return "data:image/svg+xml;charset=utf-8," + urllib.parse.quote(raw)
