"""Detecção de controladores seriais (Arduino/CH340...). A porta NUNCA é fixa: o Portal escolhe entre as detectadas, por
identificador estável (/dev/serial/by-id), e o agente resolve para o dispositivo atual. Somente caminhos de portas seriais são aceitos."""
import glob
import os
import re

PORT_ID_RE = re.compile(r"^(by-id:[A-Za-z0-9_.:\-]{1,150}|dev:tty(USB|ACM)\d{1,3})$")
ARDUINO_HINTS = ("1a86", "2341", "2a03", "0403", "10c4", "ch340", "arduino", "ftdi", "cp210")


class RealSerial:
    """Backend real: lê /dev/serial/by-id (estável) e /dev/ttyUSB*/ttyACM*; usa pyserial se instalado para vid/pid/serial."""

    def list_ports(self) -> list[dict]:
        out, seen = [], set()
        for link in sorted(glob.glob("/dev/serial/by-id/*")):
            real = os.path.realpath(link)
            name = os.path.basename(link)
            seen.add(real)
            out.append(self._entry(f"by-id:{name}", real, name, stable=True))
        for dev in sorted(glob.glob("/dev/ttyUSB*") + glob.glob("/dev/ttyACM*")):
            if os.path.realpath(dev) not in seen:
                out.append(self._entry(f"dev:{os.path.basename(dev)}", dev, os.path.basename(dev), stable=False))
        try:  # enriquecimento opcional
            from serial.tools import list_ports  # type: ignore
            info = {p.device: p for p in list_ports.comports()}
            for e in out:
                p = info.get(e["device"])
                if p:
                    e.update(vendorId=f"{p.vid:04x}" if p.vid else None, productId=f"{p.pid:04x}" if p.pid else None, serial=p.serial_number,
                             description=p.description or e["description"])
                    e["hint"] = _hint(e)
        except Exception:  # noqa: BLE001
            pass
        return out

    @staticmethod
    def _entry(pid: str, device: str, name: str, stable: bool) -> dict:
        e = {"id": pid, "device": device, "description": name, "stable": stable, "vendorId": None, "productId": None, "serial": None}
        e["hint"] = _hint(e)
        return e

    def resolve(self, port_id: str) -> str | None:
        if not PORT_ID_RE.match(port_id or ""):
            return None
        path = f"/dev/serial/by-id/{port_id[6:]}" if port_id.startswith("by-id:") else f"/dev/{port_id[4:]}"
        return os.path.realpath(path) if os.path.exists(path) else None


def _hint(e: dict) -> str:
    blob = " ".join(str(e.get(k) or "") for k in ("description", "vendorId", "id")).lower()
    return "arduino_candidate" if any(h in blob for h in ARDUINO_HINTS) else "unknown"


class SimSerial:
    """Backend de simulação (testes/homologação sem hardware): portas e resolução scriptadas. NÃO toca em dispositivos reais."""

    def __init__(self, ports: list[dict] | None = None):
        self.ports = ports if ports is not None else [
            {"id": "by-id:usb-1a86_USB_Serial-if00-port0", "device": "/dev/ttyUSB0", "description": "CH340 simulado", "stable": True,
             "vendorId": "1a86", "productId": "7523", "serial": None, "hint": "arduino_candidate"}]

    def list_ports(self) -> list[dict]:
        return list(self.ports)

    def resolve(self, port_id: str) -> str | None:
        if not PORT_ID_RE.match(port_id or ""):
            return None
        return next((p["device"] for p in self.ports if p["id"] == port_id), None)


class Devices:
    """Fachada injetável (service.py usa RealSerial; testes injetam SimSerial)."""

    def __init__(self, serial=None):
        self.serial = serial or RealSerial()

    def scan(self) -> dict:
        return {"serial": self.serial.list_ports()}
