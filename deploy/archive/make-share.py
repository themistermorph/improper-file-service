"""Legt einen öffentlichen Freigabelink für eine vorhandene Datei an und prüft ihn.

Zugangsdaten über Umgebungsvariablen:
    IFS_HTTP_HOST, IFS_HTTP_USER, IFS_HTTP_PASS
"""

from __future__ import annotations

import os

import httpx

base = os.environ["IFS_HTTP_HOST"].rstrip("/")
user = os.environ["IFS_HTTP_USER"]
password = os.environ["IFS_HTTP_PASS"]

with httpx.Client(base_url=base, timeout=30) as client:
    token = client.post(
        "/api/auth/login", json={"username": user, "password": password}
    ).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    # Wurzel durchsuchen und die erste Datei finden
    entry = None
    for top in client.get("/api/entries", headers=headers).json():
        if top["type"] == "file":
            entry = top
            break
        for child in client.get(
            "/api/entries", headers=headers, params={"parent_id": top["id"]}
        ).json():
            if child["type"] == "file":
                entry = child
                break
        if entry:
            break

    if entry is None:
        response = client.put(
            "/api/uploads/simple", headers=headers, params={"name": "http-demo.txt"},
            content=b"Zugriff ueber HTTP",
        )
        entry = response.json()

    share = client.post("/api/shares", headers=headers, json={"entry_id": entry["id"]}).json()
    url = f"{base}/api/shares/{share['token']}/content"

    print("Datei:        ", entry["name"], f"({entry.get('size')} Bytes)")
    print("Freigabe-URL: ", url)

    # Öffentlicher Abruf ohne Login
    public = httpx.get(url, timeout=30)
    print("HTTP-Status:  ", public.status_code)
    print("Inhalt:       ", public.content)
