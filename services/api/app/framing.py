"""Composição de moldura (PNG com janela transparente) sobre o replay — GEOMETRIA e FILTROS FFmpeg, só biblioteca padrão.

ARQUIVO VENDORIZADO: a cópia canônica fica aqui (API central); `services/local-agent/replay_agent/framing.py` é idêntica
(o teste `test_framing_in_sync` falha se divergirem). O Portal usa esta geometria na prévia (JS espelha `compute`) e o
agente local a usa na exportação — assim a prévia é fiel ao resultado.

Regras:
  * a arte da moldura NUNCA é esticada: mantém a proporção original (fit contain/cover/width/height + escala + deslocamento);
  * o vídeo aparece na JANELA da moldura (área transparente interna, detectada no upload e ajustável);
  * o que sobra da tela (laterais/topo) recebe fundo: 'blur' (vídeo desfocado), 'black' ou cor sólida;
  * a moldura entra por CIMA do vídeo; o arquivo de origem e o buffer circular nunca são alterados.
"""
import re

FIT_FRAME = ("contain", "cover", "width", "height")
FIT_VIDEO = ("cover", "contain")
BG_RE = re.compile(r"^(blur|black|color:#[0-9a-fA-F]{6})$")
MAX_SIDE = 4096
SIZE_RE = re.compile(r"^(\d{3,4})x(\d{3,4})$")


def parse_size(s: str) -> tuple[int, int]:
    m = SIZE_RE.match(str(s))
    if not m:
        raise ValueError("resolução inválida (use LARGURAxALTURA, ex.: 1920x1080)")
    w, h = int(m.group(1)), int(m.group(2))
    if not (240 <= w <= MAX_SIDE and 240 <= h <= MAX_SIDE):
        raise ValueError(f"resolução fora dos limites (240–{MAX_SIDE} px por lado)")
    return w, h


def default_layout() -> dict:
    return {"frameFit": "contain", "frameScale": 1.0, "dx": 0, "dy": 0, "margin": {"top": 0, "right": 0, "bottom": 0, "left": 0},
            "videoFit": "cover", "videoZoom": 1.0, "vdx": 0, "vdy": 0, "background": "blur"}


def _num(v, lo, hi, name) -> float:
    try:
        f = float(v)
    except (TypeError, ValueError):
        raise ValueError(f"{name} inválido") from None
    if not (lo <= f <= hi):
        raise ValueError(f"{name} deve estar entre {lo} e {hi}")
    return f


def validate_layout(layout: dict | None) -> dict:
    """Normaliza e valida (campos desconhecidos são rejeitados). Levanta ValueError com mensagem em português."""
    base = default_layout()
    layout = layout or {}
    unknown = set(layout) - set(base)
    if unknown:
        raise ValueError("campos desconhecidos na composição: " + ", ".join(sorted(unknown)))
    out = {**base, **layout}
    if out["frameFit"] not in FIT_FRAME or out["videoFit"] not in FIT_VIDEO:
        raise ValueError("ajuste de moldura/vídeo inválido")
    if not BG_RE.match(str(out["background"])):
        raise ValueError("fundo inválido (blur, black ou color:#RRGGBB)")
    out["frameScale"] = _num(out["frameScale"], 0.3, 3.0, "escala da moldura")
    out["videoZoom"] = _num(out["videoZoom"], 0.5, 4.0, "zoom do vídeo")
    for k in ("dx", "dy", "vdx", "vdy"):
        out[k] = int(_num(out[k], -MAX_SIDE, MAX_SIDE, k))
    m = {**base["margin"], **(out.get("margin") or {})}
    if set(m) - set(base["margin"]):
        raise ValueError("margens inválidas")
    out["margin"] = {k: int(_num(m[k], 0, 1000, "margem " + k)) for k in base["margin"]}
    return out


def validate_window(win: dict, fw: int, fh: int) -> dict:
    try:
        w = {k: int(win[k]) for k in ("x", "y", "w", "h")}
    except (KeyError, TypeError, ValueError):
        raise ValueError("janela do vídeo inválida") from None
    if w["w"] < 16 or w["h"] < 16 or w["x"] < 0 or w["y"] < 0 or w["x"] + w["w"] > fw or w["y"] + w["h"] > fh:
        raise ValueError("janela do vídeo fora da imagem da moldura")
    return w


def _even(v: float) -> int:
    return max(2, int(round(v / 2.0)) * 2)


def compute(layout: dict, out_w: int, out_h: int, fw: int, fh: int, window: dict) -> dict:
    """Retângulos (px da tela de saída) da moldura e da janela do vídeo. Determinístico; o JS do Portal espelha estas contas."""
    lay = validate_layout(layout)
    m = lay["margin"]
    bx, by = m["left"], m["top"]
    bw, bh = max(2, out_w - m["left"] - m["right"]), max(2, out_h - m["top"] - m["bottom"])
    fit = lay["frameFit"]
    s = {"contain": min(bw / fw, bh / fh), "cover": max(bw / fw, bh / fh), "width": bw / fw, "height": bh / fh}[fit] * lay["frameScale"]
    w_, h_ = _even(fw * s), _even(fh * s)
    x_ = round(bx + (bw - w_) / 2 + lay["dx"])
    y_ = round(by + (bh - h_) / 2 + lay["dy"])
    k = w_ / fw
    kh = h_ / fh
    win = {"x": round(x_ + window["x"] * k), "y": round(y_ + window["y"] * kh), "w": max(2, round(window["w"] * k)), "h": max(2, round(window["h"] * kh))}
    return {"out": {"w": out_w, "h": out_h}, "frame": {"x": x_, "y": y_, "w": w_, "h": h_}, "window": win}


def video_geometry(win: dict, vw: int, vh: int, layout: dict) -> dict:
    """Escala do vídeo dentro da janela + recorte visível. Retorna px de escala, recorte (relativo ao vídeo escalado) e posição."""
    lay = validate_layout(layout)
    ww, wh = win["w"], win["h"]
    k = (max if lay["videoFit"] == "cover" else min)(ww / vw, wh / vh) * lay["videoZoom"]
    sw, sh = _even(vw * k), _even(vh * k)
    ox = round((ww - sw) / 2 + lay["vdx"])
    oy = round((wh - sh) / 2 + lay["vdy"])
    cx, cy = max(0, -ox), max(0, -oy)                      # recorte no vídeo escalado
    px, py = max(0, ox), max(0, oy)                        # posição dentro da janela
    cw, ch = min(sw - cx, ww - px), min(sh - cy, wh - py)
    return {"scale": {"w": sw, "h": sh}, "crop": {"x": cx, "y": cy, "w": max(2, cw), "h": max(2, ch)},
            "at": {"x": win["x"] + px, "y": win["y"] + py}}


def build_filters(geom: dict, vid: dict, layout: dict, src: str = "0:v", frame: str = "1:v", out: str = "vfr") -> list[str]:
    """Cadeia FILTER_COMPLEX: fundo -> vídeo na janela -> moldura por cima. `src`/`frame` são rótulos de entrada ([0:v], [1:v])."""
    lay = validate_layout(layout)
    W, H = geom["out"]["w"], geom["out"]["h"]
    f, sc, cr, at = geom["frame"], vid["scale"], vid["crop"], vid["at"]
    flt = []
    if lay["background"] == "blur":
        flt.append(f"[{src}]split=2[bgs][fgs]")
        flt.append(f"[bgs]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},boxblur=luma_radius=24:luma_power=2,format=yuv420p[bg]")
        fg = "[fgs]"
    else:
        color = "black" if lay["background"] == "black" else "0x" + lay["background"][7:]
        flt.append(f"color=c={color}:s={W}x{H}:r=25,format=yuv420p[bg]")
        fg = f"[{src}]"
    flt.append(f"{fg}scale={sc['w']}:{sc['h']}:flags=lanczos,crop={cr['w']}:{cr['h']}:{cr['x']}:{cr['y']},setsar=1[vw]")
    flt.append(f"[bg][vw]overlay=x={at['x']}:y={at['y']}:shortest=1[c1]")
    flt.append(f"[{frame}]format=rgba,scale={f['w']}:{f['h']}:flags=lanczos[fr]")
    flt.append(f"[c1][fr]overlay=x={f['x']}:y={f['y']}:shortest=1:format=auto,format=yuv420p[{out}]")
    return flt
