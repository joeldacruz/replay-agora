"""Teste de câmera IP feito PELO AGENTE (a central nunca acessa a rede privada). Resultados em português, sem credenciais.

Etapas: connectivity (TCP) -> auth/rtsp/stream (ffprobe) -> snapshot (1 quadro JPEG reduzido). `runner` e `connect` são
injetáveis para testes/simulação; a senha só existe em memória durante o teste e é removida de qualquer texto devolvido.
"""
import base64
import json
import socket
import subprocess
from urllib.parse import quote

from .config import redact_text

MESSAGES = {
    "ok": "Câmera conectada e transmitindo.",
    "unreachable": "Endereço inacessível: o servidor local não alcançou a câmera. Verifique IP, cabo e rede.",
    "port_closed": "Porta fechada: o equipamento respondeu, mas não aceita conexões nessa porta RTSP.",
    "auth_failed": "Falha de autenticação: usuário ou senha incorretos.",
    "invalid_stream": "Stream inválido: o caminho RTSP não existe ou não entrega vídeo.",
    "codec_incompatible": "Codec incompatível com o configurado.",
    "fps_mismatch": "Conectada, mas o FPS efetivo difere do configurado.",
    "timeout": "A câmera não respondeu dentro do tempo limite.",
    "comm_error": "Erro de comunicação com a câmera.",
}


def _run(cmd: list[str], timeout: float, capture: bool = True):
    p = subprocess.run(cmd, capture_output=True, timeout=timeout)
    return p.returncode, p.stdout, p.stderr.decode(errors="replace")


def _connect(host: str, port: int, timeout: float) -> str:
    """'ok' | 'refused' | 'timeout' | 'error'"""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return "ok"
    except ConnectionRefusedError:
        return "refused"
    except (socket.timeout, TimeoutError):
        return "timeout"
    except OSError:
        return "error"


def build_url(c: dict, mask: bool = False) -> str:
    user, pw = c.get("username") or "", c.get("password") or ""
    cred = f"{quote(user, safe='')}:{'***' if mask else quote(pw, safe='')}@" if user else ""
    return f"rtsp://{cred}{c['host']}:{int(c.get('port') or 554)}{c.get('path') or ''}"


def classify_stderr(err: str) -> str:
    e = err.lower()
    if "401" in e or "unauthorized" in e or "authorization failed" in e:
        return "auth_failed"
    if "404" in e or "not found" in e or "invalid data" in e or "no streams" in e or "could not find" in e:
        return "invalid_stream"
    if "timed out" in e or "timeout" in e:
        return "timeout"
    if "connection refused" in e:
        return "port_closed"
    return "comm_error"


def _fps(rate: str) -> float | None:
    try:
        a, b = rate.split("/")
        return round(float(a) / float(b), 2) if float(b) else None
    except (ValueError, AttributeError):
        return None


def run_camera_test(params: dict, ffprobe: str = "ffprobe", ffmpeg: str = "ffmpeg", runner=_run, connect=_connect, timeout_s: float = 12.0) -> dict:
    c = params["camera"]
    step = params.get("step", "all")
    steps: list[dict] = []

    def done(code: str, ok: bool | None = None, **extra) -> dict:
        return {"code": code, "ok": (code == "ok") if ok is None else ok, "message": MESSAGES[code], "steps": steps, **extra}

    # 1) conectividade (TCP)
    net = connect(c["host"], int(c.get("port") or 554), 3.0)
    if net != "ok":
        code = {"refused": "port_closed", "timeout": "unreachable"}.get(net, "unreachable")
        steps.append({"step": "connectivity", "ok": False, "message": MESSAGES[code]})
        return done(code)
    steps.append({"step": "connectivity", "ok": True, "message": "Porta RTSP acessível."})
    if step == "connectivity":
        return done("ok")
    # 2) ffprobe: autenticação + RTSP + parâmetros do stream
    url = build_url(c)
    cmd = [ffprobe, "-v", "error", "-rtsp_transport", c.get("transport", "tcp"), "-timeout", str(int(timeout_s * 1_000_000)), "-print_format", "json",
           "-show_streams", "-select_streams", "v:0", url]
    try:
        rc, out, err = runner(cmd, timeout_s + 3)
    except subprocess.TimeoutExpired:
        steps.append({"step": "rtsp", "ok": False, "message": MESSAGES["timeout"]})
        return done("timeout")
    except OSError:
        steps.append({"step": "rtsp", "ok": False, "message": "ffprobe indisponível no servidor local."})
        return done("comm_error")
    if rc != 0:
        code = classify_stderr(err)
        steps.append({"step": "auth" if code == "auth_failed" else "rtsp", "ok": False, "message": MESSAGES[code]})
        return done(code)
    steps.append({"step": "auth", "ok": True, "message": "Autenticação aceita."})
    try:
        s = json.loads(out)["streams"][0]
    except (ValueError, KeyError, IndexError):
        steps.append({"step": "rtsp", "ok": False, "message": MESSAGES["invalid_stream"]})
        return done("invalid_stream")
    stream = {"codec": s.get("codec_name"), "width": s.get("width"), "height": s.get("height"), "fps": _fps(s.get("avg_frame_rate") or s.get("r_frame_rate"))}
    steps.append({"step": "rtsp", "ok": True, "message": "Stream RTSP válido."})
    want_codec = {"h264": "h264", "h265": "hevc"}.get(c.get("codec") or "")
    if want_codec and stream["codec"] and stream["codec"] != want_codec:
        steps.append({"step": "stream", "ok": False, "message": f"Codec detectado: {stream['codec']} (configurado: {c.get('codec')})."})
        return done("codec_incompatible", stream=stream)
    steps.append({"step": "stream", "ok": True, "message": f"{stream['width']}×{stream['height']} @ {stream['fps']} fps ({stream['codec']})."})
    extra: dict = {"stream": stream}
    code = "ok"
    decl = c.get("fps")
    if decl and stream["fps"] and abs(stream["fps"] - float(decl)) > float(decl) * 0.15:
        code = "fps_mismatch"
        steps.append({"step": "fps", "ok": False, "message": f"FPS efetivo {stream['fps']} difere do configurado ({decl})."})
    if step in ("snapshot", "all"):
        try:
            rc2, jpg, _ = runner([ffmpeg, "-v", "error", "-nostdin", "-rtsp_transport", c.get("transport", "tcp"), "-i", url, "-frames:v", "1",
                                  "-vf", "scale='min(640,iw)':-2", "-q:v", "6", "-f", "image2pipe", "-vcodec", "mjpeg", "-"], timeout_s + 3)
            if rc2 == 0 and jpg[:3] == b"\xff\xd8\xff" and len(jpg) < 400_000:
                extra["snapshotJpegB64"] = base64.b64encode(jpg).decode()
                steps.append({"step": "snapshot", "ok": True, "message": "Imagem de teste capturada."})
            else:
                steps.append({"step": "snapshot", "ok": False, "message": "Não foi possível capturar a imagem de teste."})
        except (subprocess.TimeoutExpired, OSError):
            steps.append({"step": "snapshot", "ok": False, "message": "Tempo esgotado ao capturar a imagem de teste."})
    snap = extra.pop("snapshotJpegB64", None)
    res = done(code, ok=(code in ("ok", "fps_mismatch")), **extra)
    if snap:
        res["snapshotJpegB64"] = snap          # (devolvido à parte pelo gerenciador; nunca vai no 'result' persistido)
    return res
