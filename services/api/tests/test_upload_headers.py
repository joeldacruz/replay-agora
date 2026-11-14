"""Uploads devem ler apenas a assinatura, nunca arquivos multimídia inteiros na RAM."""
from pathlib import Path
from types import SimpleNamespace

from PIL import Image

from app import mediainfo, uploads


def test_video_audio_and_logo_validation_only_read_small_headers(tmp_path, monkeypatch):
    video = tmp_path / "amostra.mp4"
    video.write_bytes(b"\x00\x00\x00\x18ftypisom" + b"0" * 128)
    audio = tmp_path / "amostra.mp3"
    audio.write_bytes(b"ID3" + b"0" * 128)
    logo = tmp_path / "logo.png"
    Image.new("RGBA", (64, 64), (2, 3, 4, 255)).save(logo)

    def forbid_bulk_read(self):
        raise AssertionError(f"leitura integral proibida: {self}")

    monkeypatch.setattr(Path, "read_bytes", forbid_bulk_read)
    monkeypatch.setattr(mediainfo, "video_info", lambda p: SimpleNamespace(duration=2.0))
    monkeypatch.setattr(mediainfo, "audio_duration", lambda p: 2.0)

    assert uploads.validate_video(video, ".mp4").duration == 2.0
    assert uploads.validate_music(audio, ".mp3") == 2.0
    encoded, w, h = uploads.process_logo(logo)
    assert encoded.startswith(uploads.PNG_MAGIC) and (w, h) == (64, 64)
