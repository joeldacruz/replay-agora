"""Teste HTTP do MAC das cameras - banco isolado do pytest."""

P = "/api/platform"


def test_camera_mac_http(client, world, H):
    headers = H("root")

    # 1. Cadastro com MAC em letras minusculas
    r = client.post(
        f"{P}/cameras",
        headers=headers,
        json={
            "arenaId": world.A.id,
            "name": "Camera MAC Teste",
            "host": "192.168.70.101",
            "macAddress": "cc:13:f3:bb:40:48",
        },
    )

    assert r.status_code == 201, r.text
    cam = r.json()
    cid = cam["id"]

    assert cam["macAddress"] == "CC:13:F3:BB:40:48"
    print("OK: cadastro e normalizacao do MAC")

    # 2. Consulta individual
    r = client.get(f"{P}/cameras/{cid}", headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["macAddress"] == "CC:13:F3:BB:40:48"
    print("OK: consulta individual")

    # 3. Listagem da arena
    r = client.get(
        f"{P}/cameras",
        headers=headers,
        params={"arenaId": world.A.id},
    )
    assert r.status_code == 200, r.text

    payload = r.json()
    if isinstance(payload, list):
        rows = payload
    elif isinstance(payload, dict):
        rows = payload.get("items", payload.get("cameras", []))
    else:
        raise AssertionError("Formato inesperado da listagem")

    item = next(x for x in rows if x["id"] == cid)
    assert item["macAddress"] == "CC:13:F3:BB:40:48"
    print("OK: listagem da arena")

    # 4. Edicao do MAC
    r = client.patch(
        f"{P}/cameras/{cid}",
        headers=headers,
        json={
            "expectedVersion": cam["version"],
            "macAddress": "cc:13:f3:bb:40:2b",
        },
    )

    assert r.status_code == 200, r.text
    updated = r.json()
    assert updated["macAddress"] == "CC:13:F3:BB:40:2B"
    print("OK: edicao e normalizacao")

    # 5. Rejeicao de MAC invalido
    r = client.patch(
        f"{P}/cameras/{cid}",
        headers=headers,
        json={
            "expectedVersion": updated["version"],
            "macAddress": "MAC-INVALIDO",
        },
    )
    assert r.status_code == 422, r.text
    print("OK: MAC invalido rejeitado")

    # 6. Limpeza do MAC usando null
    r = client.patch(
        f"{P}/cameras/{cid}",
        headers=headers,
        json={
            "expectedVersion": updated["version"],
            "macAddress": None,
        },
    )

    assert r.status_code == 200, r.text
    assert r.json()["macAddress"] is None
    print("OK: limpeza do MAC")

    # 7. Verificacao final da persistencia
    r = client.get(f"{P}/cameras/{cid}", headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["macAddress"] is None
    print("OK: persistencia confirmada")
