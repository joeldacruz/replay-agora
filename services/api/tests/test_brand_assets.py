"""A logomarca oficial é publicada em /brand e referenciada pelos três aplicativos."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
FILES = ["logo-horizontal-480.webp", "logo-horizontal-960.webp", "logo-symbol-96.webp", "logo-symbol-192.webp",
         "favicon-32.png", "favicon-64.png", "icon-192.png", "icon-512.png", "icon-maskable-512.png", "apple-touch-icon.png"]


def test_brand_files_served_publicly(client):
    for f in FILES:
        r = client.get(f"/brand/{f}")
        assert r.status_code == 200, f
        assert r.headers["content-type"].startswith("image/"), f


def test_original_logo_is_preserved():
    assert (ROOT / "apps/shared/assets/branding/original/LOGO REPLAY AGORA.jpeg").stat().st_size > 100_000


def test_apps_reference_official_brand_not_old_assets():
    for app in ("usuario", "arena", "portal-dev"):
        text = "".join(p.read_text(encoding="utf-8") for p in (ROOT / "apps" / app).glob("*") if p.suffix in {".html", ".js", ".webmanifest", ".css"})
        assert "/brand/" in text, app
        assert "logo-mark.svg" not in text, app
