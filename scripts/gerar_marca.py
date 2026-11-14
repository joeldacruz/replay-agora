"""Gera os derivados da logomarca OFICIAL (apps/shared/assets/branding/original/LOGO REPLAY AGORA.jpeg).

Somente recorte, remoção do fundo azul-marinho (chave de cor com suavização de borda) e redimensionamento.
Nenhum redesenho: as cores e proporções do arquivo original são preservadas.
Uso:  python scripts/gerar_marca.py        (requer Pillow e numpy)
"""
from pathlib import Path

import numpy as np
from PIL import Image

OUT = Path(__file__).resolve().parents[1] / "apps" / "shared" / "assets" / "branding"
SRC = OUT / "original" / "LOGO REPLAY AGORA.jpeg"

im = Image.open(SRC).convert("RGB")
a = np.asarray(im).astype(np.float32)
edge = np.concatenate([a[:6].reshape(-1, 3), a[-6:].reshape(-1, 3), a[:, :6].reshape(-1, 3), a[:, -6:].reshape(-1, 3)])
BG = np.median(edge, axis=0)                                   # azul-marinho do arquivo original
dist = np.sqrt(((a - BG) ** 2).sum(2))
alpha = np.clip((dist - 45) / (120 - 45), 0, 1)                # piso 45: ruído JPEG; 120: opaco
alpha[dist < 45] = 0
with np.errstate(divide="ignore", invalid="ignore"):
    fg = BG + (a - BG) / np.maximum(alpha, 1e-3)[..., None]    # desfaz a mistura com o fundo nas bordas
fg = np.clip(fg, 0, 255)
fg[alpha >= 1] = a[alpha >= 1]
full = Image.fromarray(np.dstack([fg, alpha * 255]).astype(np.uint8), "RGBA")


def crop(x0, x1, pad):
    yy, xx = np.where(alpha[:, x0:x1] > 0.5)
    return full.crop((max(x0 + xx.min() - pad, 0), max(yy.min() - pad, 0), min(x0 + xx.max() + pad + 1, full.width), min(yy.max() + pad + 1, full.height)))


cols = np.where((alpha > 0.5).any(0))[0]
split = cols[np.where(np.diff(cols) > 30)[0][0] + 1]           # vão entre símbolo e texto
lockup = crop(0, full.width, 8)                                # símbolo + "Replay / Agora"
symbol = crop(0, split - 10, 6)                                # somente o símbolo


def scaled(img, w):
    return img.resize((w, round(img.height * w / img.width)), Image.LANCZOS)


def save_webp(img, name):
    img.save(OUT / name, "WEBP", quality=92, method=6)


for w in (480, 960):
    save_webp(scaled(lockup, w), f"logo-horizontal-{w}.webp")
for w in (96, 192):
    save_webp(scaled(symbol, w), f"logo-symbol-{w}.webp")


def icon(size, fill):
    """Ícone quadrado com fundo azul-marinho e o símbolo oficial centralizado (fill = fração do lado)."""
    tile = Image.new("RGB", (size, size), tuple(int(v) for v in BG))
    s = scaled(symbol, round(size * fill))
    tile.paste(s, ((size - s.width) // 2, (size - s.height) // 2), s)
    return tile


icon(192, .62).save(OUT / "icon-192.png", optimize=True)
icon(512, .62).save(OUT / "icon-512.png", optimize=True)
icon(512, .46).save(OUT / "icon-maskable-512.png", optimize=True)   # zona segura de ícone adaptativo
icon(180, .62).save(OUT / "apple-touch-icon.png", optimize=True)
fav = scaled(symbol, 32); canvas = Image.new("RGBA", (32, 32), (0, 0, 0, 0)); canvas.paste(fav, (0, (32 - fav.height) // 2), fav)
canvas.save(OUT / "favicon-32.png", optimize=True)
fav = scaled(symbol, 64); canvas = Image.new("RGBA", (64, 64), (0, 0, 0, 0)); canvas.paste(fav, (0, (64 - fav.height) // 2), fav)
canvas.save(OUT / "favicon-64.png", optimize=True)
print("lockup", lockup.size, "símbolo", symbol.size, "fundo", BG)
