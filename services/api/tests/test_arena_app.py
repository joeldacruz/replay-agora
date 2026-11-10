import io

from PIL import Image


def png(w=200, h=100, color=(10, 200, 30, 255)):
    b = io.BytesIO()
    Image.new("RGBA", (w, h), color).save(b, "PNG")
    return b.getvalue()


def A(world):
    return f"/api/arena/{world.A.id}"


def test_operator_vs_admin_permissions(client, world, H):
    op, base = H("a_op_arena"), A(world)
    assert client.get(f"{base}/dashboard", headers=op).status_code == 200
    assert client.get(f"{base}/courts", headers=op).status_code == 200
    assert client.patch(f"{base}/videos/{world.A.priv}", headers=op, json={"visibility": "arena"}).status_code == 403  # publicação é automática: sem aprovação manual
    # operador NÃO administra
    assert client.post(f"{base}/sponsors", headers=op, data={"name": "Nova Marca"}, files={"logo": ("l.png", png(), "image/png")}).status_code == 403
    assert client.post(f"{base}/courts", headers=op, json={"label": "Q3", "localCourtKey": "A_03"}).status_code == 403
    assert client.post(f"{base}/music", headers=op).status_code == 403
    assert client.delete(f"{base}/videos/{world.A.pub}?confirm=true", headers=op).status_code == 403
    assert client.get(f"{base}/members", headers=op).status_code == 403
    assert client.get(f"{base}/audit", headers=op).status_code == 403
    assert client.patch(base, headers=op, json={"name": "x" * 5}).status_code == 403


def test_dashboard_metrics(client, world, H):
    d = client.get(f"{A(world)}/dashboard", headers=H("a_admin_arena")).json()
    assert d["videos"] == 3 and d["published"] == 2 and d["pendingReview"] == 1
    assert len(d["courts"]) == 2 and d["storageBytes"] > 0 and d["storageLimitMb"] == 1024


def test_courts_crud_and_plan_limit_and_no_credentials(client, world, H):
    assert client.post(f"{A(world)}/courts", headers=H("a_admin_arena"), json={"label": "Q9", "localCourtKey": "A_09"}).status_code == 403   # gestor não cadastra quadras
    h, base = H("root"), A(world)    # quadras: administração global (Portal / sessão de plataforma + MFA)
    bad = client.post(f"{base}/courts", headers=h, json={"label": "Q3", "localCourtKey": "A_03", "cameraRef": "rtsp://user:senha@10.0.0.1/stream"})
    assert bad.status_code == 422
    ok = client.post(f"{base}/courts", headers=h, json={"label": "Q3", "localCourtKey": "A_03", "cameraRef": "camera-03"})
    assert ok.status_code == 201
    assert client.post(f"{base}/courts", headers=h, json={"label": "Q4", "localCourtKey": "A_04"}).status_code == 409  # plano: máx. 3
    assert client.post(f"{base}/courts", headers=h, json={"label": "dup", "localCourtKey": "A_03"}).status_code == 409
    assert client.patch(f"{base}/courts/{ok.json()['id']}", headers=h, json={"status": "maintenance", "retentionDays": 7}).json()["status"] == "maintenance"
    assert client.patch(f"{base}/courts/{ok.json()['id']}", headers=h, json={"cameraRef": "rtsp://a:b@h/x"}).status_code == 422


def test_sponsor_logo_validation(client, world, H):
    h, base = H("a_admin_arena"), A(world)
    post = lambda name, content, ct="image/png": client.post(f"{base}/sponsors", headers=h, data={"name": name}, files={"logo": (name + ".png", content, ct)})  # noqa: E731
    assert post("Marca Boa", png()).status_code == 201
    svg = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'
    assert post("Svg Mal", svg, "image/svg+xml").status_code == 422
    assert post("Falso Png", b"MZ\x90\x00 executavel disfarcado").status_code == 422
    assert post("Png Cortado", png()[:60]).status_code == 422
    assert post("Gigante", png(2500, 100)).status_code == 422
    assert post("Minusculo", png(10, 10)).status_code == 422
    assert post("Vazio", b"").status_code == 422
    big = io.BytesIO()
    Image.effect_noise((900, 900), 100).convert("RGBA").save(big, "PNG")
    assert post("Pesado", big.getvalue()).status_code in (413, 422)
    r = client.post(f"{base}/sponsors", headers=h, data={"name": "Site Ruim", "website": "javascript:alert(1)"}, files={"logo": ("l.png", png(), "image/png")})
    assert r.status_code == 422


def test_sponsor_logo_is_reencoded_and_stored_privately(client, world, H):
    h, base = H("a_admin_arena"), A(world)
    meta = io.BytesIO()
    from PIL import PngImagePlugin
    info = PngImagePlugin.PngInfo()
    info.add_text("Comment", "SEGREDO-NO-METADADO")
    Image.new("RGBA", (200, 100), (1, 2, 3, 255)).save(meta, "PNG", pnginfo=info)
    s = client.post(f"{base}/sponsors", headers=h, data={"name": "Meta Marca"}, files={"logo": ("l.png", meta.getvalue(), "image/png")}).json()
    data = client.get(s["logoUrl"]).content
    assert b"SEGREDO-NO-METADADO" not in data and data[:8] == b"\x89PNG\r\n\x1a\n"
    assert "/arenas/" not in s["logoUrl"]  # só URL assinada


def test_sponsor_swap_logo_and_campaign_preview(client, world, H):
    h, base = H("a_admin_arena"), A(world)
    r = client.patch(f"{base}/sponsors/{world.A.sponsor}", headers=h, data={"name": "Renomeada"}, files={"logo": ("n.png", png(300, 120, (0, 0, 255, 255)), "image/png")})
    assert r.status_code == 200 and r.json()["name"] == "Renomeada" and r.json()["logoWidth"] == 300
    pv = client.post(f"{base}/campaigns/preview", headers=h, json={"sponsorId": world.A.sponsor, "position": "bottom-right", "sizePct": 20, "opacity": 1.0, "videoId": world.A.pub})
    assert pv.status_code == 200 and pv.headers["content-type"] == "image/png"
    im = Image.open(io.BytesIO(pv.content)).convert("RGB")
    assert im.width == 1280
    px = im.getpixel((im.width - 26 - 100, im.height - 26 - 20))  # dentro do logo azul, canto inferior direito
    assert px[2] > 200 and px[0] < 60
    assert client.post(f"{base}/campaigns/preview", headers=h, json={"sponsorId": world.A.sponsor, "position": "meio"}).status_code in (422,)


def test_campaign_validation_and_effect_on_new_exports(client, world, H):
    h, base = H("a_admin_arena"), A(world)
    mk = lambda **k: client.post(f"{base}/campaigns", headers=h, json={"sponsorId": world.A.sponsor, **k})  # noqa: E731
    assert mk(position="centro").status_code == 422
    assert mk(sizePct=90).status_code == 422
    assert mk(opacity=0).status_code == 422
    assert mk(startsOn="2026-12-10T00:00:00", endsOn="2026-12-01T00:00:00").status_code == 422
    r = mk(position="bottom-left", courtId=world.A.c2, priority=5)
    assert r.status_code == 201
    uh = H("a_user")
    d1 = client.get(f"/api/videos/{world.A.mute}", headers=uh).json()  # quadra 2
    d2 = client.get(f"/api/videos/{world.A.pub}", headers=uh).json()  # quadra 1
    assert d1["sponsors"] and d2["sponsors"]
    e1 = client.post(f"/api/videos/{world.A.mute}/exports", headers=uh, json={"audioMode": "mute"}).json()
    e2 = client.post(f"/api/videos/{world.A.pub}/exports", headers=uh, json={"audioMode": "mute"}).json()
    from app.db import SessionLocal
    from app.models import ExportJob
    import json
    db = SessionLocal()
    s1 = {o["position"] for o in json.loads(db.get(ExportJob, e1["id"]).overlay_spec)}
    s2 = {o["position"] for o in json.loads(db.get(ExportJob, e2["id"]).overlay_spec)}
    assert s1 == {"top-left", "bottom-left"} and s2 == {"top-left"}  # campanha da quadra 2 não vaza para a quadra 1
    # desativar a campanha: novos exports não a incluem; o snapshot do job antigo permanece
    client.patch(f"{base}/campaigns/{r.json()['id']}", headers=h, json={"active": False})
    e3 = client.post(f"/api/videos/{world.A.mute}/exports", headers=uh, json={"audioMode": "mute", "originalVolume": 1.0, "musicVolume": 0.61}).json()
    assert {o["position"] for o in json.loads(db.get(ExportJob, e3["id"]).overlay_spec)} == {"top-left"}
    assert s1 == {"top-left", "bottom-left"}
    db.close()


def test_campaign_date_window_and_inactive_sponsor(client, world, H):
    h, base = H("a_admin_arena"), A(world)
    client.patch(f"{base}/campaigns/{world.A.campaign}", headers=h, json={"endsOn": "2000-01-01T00:00:00", "startsOn": "1999-01-01T00:00:00"})
    assert client.get(f"/api/videos/{world.A.pub}", headers=H("a_user")).json()["sponsors"] == []
    client.patch(f"{base}/campaigns/{world.A.campaign}", headers=h, json={"endsOn": None, "startsOn": None})
    assert client.get(f"/api/videos/{world.A.pub}", headers=H("a_user")).json()["sponsors"]
    client.patch(f"{base}/sponsors/{world.A.sponsor}", headers=h, data={"status": "inactive"})
    assert client.get(f"/api/videos/{world.A.pub}", headers=H("a_user")).json()["sponsors"] == []


def _mp3(media):
    return media["tone"].read_bytes()


def test_music_upload_rules(client, world, H, media):
    h, base = H("a_admin_arena"), A(world)
    form = {"title": "Faixa Nova", "artist": "Autor", "license_info": "Licença CC0 comprovada em contrato 42", "rights_declared": "true",
            "allowed_use": "play,export,social", "license_valid_until": "2099-12-31"}
    post = lambda data=form, name="a.mp3", content=None: client.post(f"{base}/music", headers=h, data=data, files={"file": (name, content or _mp3(media), "audio/mpeg")})  # noqa: E731
    r = post()
    assert r.status_code == 201 and r.json()["usable"] is True and r.json()["duration"] > 1
    assert post(name="a.exe").status_code == 422
    assert post(content=b"isto nao e audio" * 50).status_code == 422  # extensão mp3 mas conteúdo falso
    assert post(name="v.mp3", content=media["audio"].read_bytes()).status_code == 422  # contém vídeo / assinatura errada
    assert post(data={**form, "license_info": "curto"}).status_code == 422  # licença documentada é obrigatória
    assert post(data={**form, "allowed_use": "download-pirata"}).status_code == 422
    nr = post(data={**form, "rights_declared": "false", "title": "Sem Direitos"})
    assert nr.status_code == 201 and nr.json()["usable"] is False
    exp = post(data={**form, "license_valid_until": "2001-01-01", "title": "Vencida"})
    assert exp.json()["usable"] is False
    # só a faixa válida aparece para o atleta
    titles = [t["title"] for t in client.get(f"/api/videos/{world.A.pub}/export-options", headers=H("a_user")).json()["tracks"]]
    assert "Faixa Nova" in titles and "Sem Direitos" not in titles and "Vencida" not in titles
    # atleta não vê dados de licença (nem a rota admin)
    assert client.get(f"{base}/music", headers=H("a_user")).status_code == 403


def test_music_disable_removes_from_athlete_options(client, world, H):
    h, base = H("a_admin_arena"), A(world)
    client.patch(f"{base}/music/{world.A.tracks['ok']}", headers=h, json={"status": "disabled"})
    o = client.get(f"/api/videos/{world.A.pub}/export-options", headers=H("a_user")).json()
    assert o["tracks"] == [] and "music" not in o["modes"] and "mix" not in o["modes"]


def test_default_audio_policy_validation(client, world, H):
    h, base = H("a_admin_arena"), A(world)
    assert client.patch(base, headers=h, json={"allowedModes": []}).status_code == 422
    assert client.patch(base, headers=h, json={"allowedModes": ["original", "x"]}).status_code == 422
    assert client.patch(base, headers=h, json={"defaultMode": "mix", "allowedModes": ["original", "mute"]}).status_code == 422
    assert client.patch(base, headers=h, json={"originalVolume": 5}).status_code == 422
    ok = client.patch(base, headers=h, json={"defaultMode": "mix", "musicVolume": 0.4, "originalVolume": 0.9, "defaultMusicId": world.A.tracks["ok"]})
    assert ok.status_code == 200 and ok.json()["audio"]["defaultMode"] == "mix"
    assert client.delete(f"{base}/music/{world.A.tracks['ok']}", headers=h).status_code == 204
    assert client.get(base, headers=h).json()["audio"]["defaultMusicId"] is None


def test_members_and_last_admin_guard(client, world, H):
    h, base = H("a_admin_arena"), A(world)
    members = client.get(f"{base}/members", headers=h).json()
    me = next(m for m in members if m["email"] == world.emails["a_admin"])
    assert client.patch(f"{base}/members/{me['id']}", headers=h, json={"role": "user"}).status_code == 409
    assert client.delete(f"{base}/members/{me['id']}", headers=h).status_code == 409
    assert client.post(f"{base}/members", headers=h, json={"email": world.emails["nobody"], "role": "arena_operator"}).status_code == 201
    assert client.post(f"{base}/members", headers=h, json={"email": world.emails["nobody"], "role": "arena_operator"}).status_code == 409
    assert client.post(f"{base}/members", headers=h, json={"email": world.emails["nobody"], "role": "global_admin"}).status_code == 422
    assert client.post(f"{base}/members", headers=h, json={"email": "fantasma@t.test", "role": "user"}).status_code == 404
    # não pode mexer em membro de outra arena (id de B)
    other = client.get(f"/api/arena/{world.B.id}/members", headers=H("b_admin_arena")).json()[0]["id"]
    assert client.patch(f"{base}/members/{other}", headers=h, json={"role": "user"}).status_code == 404
    assert client.delete(f"{base}/members/{other}", headers=h).status_code == 404


def test_delete_video_requires_confirm_and_removes_files(client, world, H):
    from app import storage
    from app.db import SessionLocal
    from app.models import Video
    assert client.delete(f"{A(world)}/videos/{world.A.priv}?confirm=true", headers=H("a_admin_arena")).status_code == 403   # gestor não exclui replays
    h, base = H("root"), A(world)
    db = SessionLocal()
    key = db.get(Video, world.A.priv).storage_key
    db.close()
    assert storage.path_for(key).exists()
    assert client.delete(f"{base}/videos/{world.A.priv}", headers=h).status_code == 422
    assert client.delete(f"{base}/videos/{world.A.priv}?confirm=true", headers=h).status_code == 204
    assert not storage.path_for(key).exists()
    assert world.A.priv not in [v["id"] for v in client.get(f"{base}/videos", headers=h).json()]


def test_audit_trail_records_sponsor_music_and_video_removal(client, world, H, media):
    h, base, g = H("a_admin_arena"), A(world), H("root")
    client.post(f"{base}/sponsors", headers=h, data={"name": "Auditada"}, files={"logo": ("l.png", png(), "image/png")})
    client.patch(f"{base}/campaigns/{world.A.campaign}", headers=h, json={"priority": 3})
    client.patch(f"{base}/music/{world.A.tracks['ok']}", headers=h, json={"rightsDeclared": False})
    client.delete(f"{base}/videos/{world.A.priv}?confirm=true", headers=g)
    client.post(f"{base}/agents", headers=g, json={"label": "agente novo"})
    assert client.get(f"{base}/audit", headers=h).status_code == 403     # auditoria: administrador global
    log = client.get(f"{base}/audit", headers=g).json()
    actions = {e["action"] for e in log}
    assert {"sponsor.create", "campaign.update", "music.update", "video.delete", "agent.create"} <= actions
    assert all(e["actor"] == "A Admin" for e in log if e["action"].startswith("sponsor"))
    assert all(e["actor"] == "Root" for e in log if e["action"] == "video.delete")
    assert "SEGREDO" not in str(log) and "rag_" not in str(log)  # nenhum segredo/token em auditoria
    # auditoria da arena B não mistura
    assert not any(e["resourceId"] == world.A.priv for e in client.get(f"/api/arena/{world.B.id}/audit", headers=g).json())


def test_agent_token_shown_once_and_revocable(client, world, H):
    assert client.post(f"{A(world)}/agents", headers=H("a_admin_arena"), json={"label": "x"}).status_code == 403   # gestor não cria credenciais de agente
    h, base = H("root"), A(world)
    t = client.post(f"{base}/agents", headers=h, json={"label": "motor da quadra"}).json()
    assert t["token"].startswith("rag_")
    assert all("token" not in a for a in client.get(f"{base}/agents", headers=h).json())
    ev = {"courtKey": "A_01", "idempotencyKey": "evt-agent-0001", "capturedAt": "2026-11-04T12:00:00Z"}
    assert client.post("/api/ingest/events", headers={"Authorization": f"Bearer {t['token']}"}, json=ev).status_code == 200
    assert client.delete(f"{base}/agents/{t['id']}", headers=h).status_code == 204
    assert client.post("/api/ingest/events", headers={"Authorization": f"Bearer {t['token']}"}, json=ev | {"idempotencyKey": "evt-agent-0002"}).status_code == 401
