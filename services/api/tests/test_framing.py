"""Geometria de composição de molduras: cópia vendorizada idêntica, proporção preservada, espelho em JS e comando FFmpeg do agente."""
import json
import subprocess
from pathlib import Path

import pytest

from app import framing as F
from replay_agent import framing as AF
from replay_agent.ffplan import ExportPlan, FramePlan, Overlay, SourceMeta, build_command

ROOT = Path(__file__).resolve().parents[3]
WIN_H = {"x": 23, "y": 223, "w": 1081, "h": 499}
LAYOUTS = [F.default_layout(), {"frameFit": "cover", "frameScale": 1.2, "dx": 15, "dy": -9, "margin": {"top": 10, "right": 20, "bottom": 30, "left": 40}, "videoFit": "contain", "videoZoom": 1.3, "vdx": -33, "vdy": 21, "background": "black"},
           {"frameFit": "width", "frameScale": 0.75, "videoZoom": 2.5, "vdx": 70, "background": "color:#112233"}, {"frameFit": "height", "frameScale": 1.0}]
SIZES = [(1920, 1080, 1118, 904), (1080, 1920, 553, 940), (1280, 720, 1118, 904), (1081, 1921, 553, 940)]


def test_framing_in_sync():
    """A cópia do agente deve ser IDÊNTICA à canônica da API (o agente não importa código do servidor)."""
    assert (ROOT / "services/api/app/framing.py").read_bytes() == (ROOT / "services/local-agent/replay_agent/framing.py").read_bytes()
    assert F.compute(F.default_layout(), 1920, 1080, 1118, 904, WIN_H) == AF.compute(F.default_layout(), 1920, 1080, 1118, 904, WIN_H)


@pytest.mark.parametrize("lay", LAYOUTS)
@pytest.mark.parametrize("ow,oh,fw,fh", SIZES)
def test_frame_is_never_stretched_and_window_stays_inside_frame(lay, ow, oh, fw, fh):
    win = {"x": fw // 20, "y": fh // 4, "w": fw * 9 // 10, "h": fh // 2}
    g = F.compute(lay, ow, oh, fw, fh, win)
    assert abs(g["frame"]["w"] / g["frame"]["h"] - fw / fh) < 0.012                       # proporção da arte preservada (±arredondamento par)
    fr, w = g["frame"], g["window"]
    assert fr["x"] <= w["x"] and w["x"] + w["w"] <= fr["x"] + fr["w"] + 2 and fr["y"] <= w["y"] and w["y"] + w["h"] <= fr["y"] + fr["h"] + 2
    v = F.video_geometry(w, 1280, 720, lay)
    assert v["crop"]["w"] <= w["w"] and v["crop"]["h"] <= w["h"] and v["scale"]["w"] % 2 == 0 and v["scale"]["h"] % 2 == 0
    assert abs(v["scale"]["w"] / v["scale"]["h"] - 1280 / 720) < 0.02                      # o vídeo também não é deformado


def test_layout_and_size_validation():
    for bad in ({"frameFit": "stretch"}, {"naoExiste": 1}, {"background": "url(x)"}, {"frameScale": 9}, {"margin": {"top": -1}}, {"videoZoom": 0}):
        with pytest.raises(ValueError):
            F.validate_layout(bad)
    for bad in ("1920", "99999x10", "axb", "100x100"):
        with pytest.raises(ValueError):
            F.parse_size(bad)
    assert F.parse_size("1080x1920") == (1080, 1920)


def test_javascript_preview_mirrors_python_geometry(tmp_path):
    """O editor do Portal calcula a mesma geometria que o agente (prévia fiel)."""
    cases = [{"layout": F.validate_layout(l), "ow": ow, "oh": oh, "fw": fw, "fh": fh, "win": {"x": fw // 20, "y": fh // 4, "w": fw * 9 // 10, "h": fh // 2}} for l in LAYOUTS for ow, oh, fw, fh in SIZES]
    js = tmp_path / "t.mjs"
    js.write_text(f"import {{ computeGeometry, videoGeometry }} from '{ROOT}/apps/portal-dev/views-frames.js';\n"
                  f"const cases = {json.dumps(cases)};\nconsole.log(JSON.stringify(cases.map(c => {{ const g = computeGeometry(c.layout, c.ow, c.oh, c.fw, c.fh, c.win); return [g, videoGeometry(g.window, 1280, 720, c.layout)]; }})));\n")
    out = json.loads(subprocess.run(["node", str(js)], capture_output=True, text=True, check=True).stdout)
    for c, (g, v) in zip(cases, out):
        pg = F.compute(c["layout"], c["ow"], c["oh"], c["fw"], c["fh"], c["win"])
        assert g["frame"] == pg["frame"] and g["window"] == pg["window"], c
        pv = F.video_geometry(pg["window"], 1280, 720, c["layout"])
        assert v["scale"] == pv["scale"] and v["crop"] == pv["crop"] and v["at"] == pv["at"], c


def _plan(frame=None, overlays=()):
    meta = SourceMeta(3.0, 640, 360, "h264", True, "aac")
    return ExportPlan("/x/src.mp4", "/x/out.mp4", meta, "original", None, 1.0, 0.6, list(overlays), frame)


def test_ffplan_with_frame_uses_shared_filters_and_keeps_audio_and_sponsors():
    fp = FramePlan("/x/frame.png", 1118, 904, WIN_H, F.default_layout(), 1920, 1080)
    cmd = build_command(_plan(fp))
    fc = cmd[cmd.index("-filter_complex") + 1]
    assert "overlay=" in fc and "[vfr]" in fc and ["-map", "[vfr]"] == cmd[cmd.index("[vfr]") - 1: cmd.index("[vfr]") + 1]
    assert cmd.count("-i") == 2 and "/x/frame.png" in cmd and "libx264" in cmd                        # recodifica (explícito); quadro de moldura é a 2ª entrada
    assert "-c:a" in cmd and "copy" in cmd                                                            # áudio original preservado (aac)
    # com patrocinador: o logo vai por cima da moldura, posicionado pela tela de SAÍDA (1920x1080), não pelo vídeo de origem
    cmd2 = build_command(_plan(fp, [Overlay("/x/logo.png", 400, 160, "top-left", 20, 1.0)]))
    fc2 = cmd2[cmd2.index("-filter_complex") + 1]
    assert "[vfr][lg0]overlay" in fc2 and "scale=384:" in fc2
    # sem moldura: comando IDÊNTICO ao anterior (sem filtros extras)
    assert "-filter_complex" not in build_command(_plan(None))
