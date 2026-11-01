#!/usr/bin/env python3
"""Publica en Instagram lo que toca según schedule.json, con la API de Instagram
(inicio de sesión con Instagram). Lo ejecuta GitHub Actions cada 10 minutos.

Uso:
  python publish.py              publica lo que ya venció (y no tiene más de MAX_LATE_HOURS de atraso)
  python publish.py --check      verifica el token y muestra la cuenta y la cuota de publicación
  python publish.py --dry-run    muestra qué se publicaría ahora, sin publicar
  python publish.py --dry-run --now 2026-11-03T19:05:00-05:00   simula otra hora

Variables de entorno:
  IG_ACCESS_TOKEN   token (secreto). Acepta dos tipos:
                      - token de usuario del sistema de Meta (empieza con EAA): usa graph.facebook.com
                      - token de inicio de sesión con Instagram (empieza con IG): usa graph.instagram.com
  IG_USER_ID        ID de la cuenta profesional de Instagram. Obligatorio con token de sistema
                    si no se puede descubrir solo; con token de Instagram se obtiene de /me.
  MEDIA_BASE_URL    URL pública donde están los archivos de media/, terminada en /
  GRAPH_VERSION     versión de la API (por defecto v23.0)
  MAX_LATE_HOURS    no publica nada con más atraso que esto (por defecto 12)
"""
import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
SCHEDULE = os.path.join(HERE, "schedule.json")
STATE = os.path.join(HERE, "state.json")
VERSION = os.environ.get("GRAPH_VERSION", "v23.0")
API = None  # se fija en configure() según el tipo de token
MAX_LATE = timedelta(hours=float(os.environ.get("MAX_LATE_HOURS", "12")))
MAX_ATTEMPTS = 3


def token():
    return os.environ.get("IG_ACCESS_TOKEN", "").strip() or read_local(".token")


def read_local(name):
    p = os.path.join(HERE, name)
    if os.path.exists(p):
        with open(p, encoding="utf-8-sig") as fh:
            return fh.read().strip()
    return ""


def configure(tok):
    """Elige la API según el token y devuelve (ig_user_id, username)."""
    global API
    if tok.startswith("IG"):
        API = f"https://graph.instagram.com/{VERSION}"
        me = call("GET", "me", {"fields": "user_id,username"}, tok)
        return me["user_id"], me.get("username")
    API = f"https://graph.facebook.com/{VERSION}"
    ig_id = os.environ.get("IG_USER_ID", "").strip() or read_local(".igid")
    if not ig_id:
        # Intento de descubrimiento: páginas con cuenta de Instagram vinculada.
        try:
            pages = call("GET", "me/accounts", {"fields": "instagram_business_account{id,username}"}, tok)
            for pg in pages.get("data", []):
                acc = pg.get("instagram_business_account")
                if acc:
                    ig_id = acc["id"]
                    break
        except RuntimeError:
            pass
    if not ig_id:
        raise RuntimeError("Falta IG_USER_ID: el ID de la cuenta de Instagram (o el archivo .igid)")
    acc = call("GET", ig_id, {"fields": "id,username"}, tok)
    return acc["id"], acc.get("username")


def call(method, path, params, tok):
    params = {**params, "access_token": tok}
    body = urllib.parse.urlencode(params)
    if method == "GET":
        req = urllib.request.Request(f"{API}/{path}?{body}")
    else:
        req = urllib.request.Request(f"{API}/{path}", data=body.encode(), method="POST")
    try:
        with urllib.request.urlopen(req, timeout=90) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")
        try:
            err = json.loads(detail).get("error", {})
            detail = f"{err.get('message')} (code {err.get('code')}, subcode {err.get('error_subcode')})"
        except ValueError:
            pass
        # Nunca se imprime la URL: lleva el token.
        raise RuntimeError(f"{method} {path.split('?')[0]} -> HTTP {e.code}: {detail}") from None


def wait_ready(cid, tok, timeout_s):
    start = time.time()
    while True:
        st = call("GET", cid, {"fields": "status_code,status"}, tok)
        code = st.get("status_code")
        if code in ("FINISHED", "PUBLISHED"):
            return
        if code in ("ERROR", "EXPIRED"):
            raise RuntimeError(f"El contenedor {cid} quedó en {code}: {st.get('status')}")
        if time.time() - start > timeout_s:
            raise RuntimeError(f"El contenedor {cid} no terminó de procesarse en {timeout_s} s (estado {code})")
        time.sleep(5)


def publish_item(item, ig_id, base_url, tok):
    urls = [base_url + m for m in item["media"]]
    kind = item["type"]
    if kind == "FEED":
        cid = call("POST", f"{ig_id}/media", {"image_url": urls[0], "caption": item.get("caption", "")}, tok)["id"]
        wait_ready(cid, tok, 120)
    elif kind == "STORY":
        if urls[0].lower().endswith(".mp4"):
            cid = call("POST", f"{ig_id}/media", {"video_url": urls[0], "media_type": "STORIES"}, tok)["id"]
            wait_ready(cid, tok, 600)
        else:
            cid = call("POST", f"{ig_id}/media", {"image_url": urls[0], "media_type": "STORIES"}, tok)["id"]
            wait_ready(cid, tok, 120)
    elif kind == "CAROUSEL":
        children = []
        for u in urls:
            c = call("POST", f"{ig_id}/media", {"image_url": u, "is_carousel_item": "true"}, tok)["id"]
            wait_ready(c, tok, 120)
            children.append(c)
        cid = call("POST", f"{ig_id}/media", {"media_type": "CAROUSEL", "children": ",".join(children),
                                               "caption": item.get("caption", "")}, tok)["id"]
        wait_ready(cid, tok, 180)
    elif kind == "REEL":
        cid = call("POST", f"{ig_id}/media", {"media_type": "REELS", "video_url": urls[0],
                                               "caption": item.get("caption", ""), "share_to_feed": "true",
                                               "thumb_offset": "1500"}, tok)["id"]
        wait_ready(cid, tok, 600)
    else:
        raise RuntimeError(f"Tipo desconocido: {kind}")
    return call("POST", f"{ig_id}/media_publish", {"creation_id": cid}, tok)["id"]


def load(path, default):
    if not os.path.exists(path):
        return default
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def save_state(state):
    with open(STATE, "w", encoding="utf-8") as fh:
        json.dump(state, fh, ensure_ascii=False, indent=2)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--now")
    a = ap.parse_args()

    now = datetime.fromisoformat(a.now) if a.now else datetime.now(timezone.utc)
    sched = load(SCHEDULE, {"items": []})
    state = load(STATE, {})
    tok = token()

    if a.check:
        if not tok:
            sys.exit("No hay token. Ponlo en IG_ACCESS_TOKEN o en el archivo .token")
        ig_id, username = configure(tok)
        info = call("GET", ig_id, {"fields": "username,media_count"}, tok)
        print(f"Token válido para @{username} (id {ig_id}, API {API.split('/')[2]}, "
              f"{info.get('media_count')} publicaciones)")
        try:
            q = call("GET", f"{ig_id}/content_publishing_limit", {"fields": "quota_usage,config"}, tok)
            print("Cuota de publicación:", json.dumps(q.get("data", q), ensure_ascii=False))
        except RuntimeError as e:
            print("No se pudo leer la cuota:", e)
        return

    if sched.get("paused"):
        print("schedule.json tiene paused=true. No se publica nada.")
        return

    due, upcoming = [], []
    for it in sched["items"]:
        st = state.get(it["id"], {})
        if st.get("status") in ("published", "skipped_late") or st.get("attempts", 0) >= MAX_ATTEMPTS:
            continue
        at = datetime.fromisoformat(it["at"])
        (due if at <= now else upcoming).append((at, it))

    if a.dry_run:
        print(f"Ahora: {now.isoformat()}")
        for at, it in due:
            late = now - at
            verb = "SE OMITIRÍA (muy atrasada)" if late > MAX_LATE else "SE PUBLICARÍA"
            print(f"  {verb}: {it['at']} {it['type']} {it['id']}")
        print(f"  Pendientes a futuro: {len(upcoming)}")
        for at, it in upcoming[:5]:
            print(f"    {it['at']} {it['type']} {it['id']}")
        return

    if not due:
        nxt = upcoming[0][1] if upcoming else None
        print("Nada que publicar ahora." + (f" Próxima: {nxt['at']} {nxt['id']}" if nxt else " Calendario completo."))
        return

    if not tok:
        print("Hay publicaciones pendientes pero falta IG_ACCESS_TOKEN. No se publica nada.")
        return
    base_url = os.environ.get("MEDIA_BASE_URL", "").strip()
    if not base_url.endswith("/"):
        sys.exit("MEDIA_BASE_URL debe ser una URL pública terminada en /")

    ig_id, username = configure(tok)
    print(f"Publicando en @{username}")
    failures = 0
    for at, it in sorted(due, key=lambda x: x[0]):
        rec = state.setdefault(it["id"], {})
        if now - at > MAX_LATE:
            rec.update(status="skipped_late", at=now.isoformat())
            print(f"OMITIDA por atraso: {it['id']} (debía salir {it['at']})")
            save_state(state)
            continue
        try:
            media_id = publish_item(it, ig_id, base_url, tok)
            rec.update(status="published", media_id=media_id, at=datetime.now(timezone.utc).isoformat())
            print(f"PUBLICADA: {it['type']} {it['id']} -> {media_id}")
        except RuntimeError as e:
            rec["attempts"] = rec.get("attempts", 0) + 1
            rec.update(status="error", error=str(e))
            failures += 1
            print(f"ERROR ({rec['attempts']}/{MAX_ATTEMPTS}) {it['id']}: {e}")
        save_state(state)
    if failures:
        sys.exit(1)


if __name__ == "__main__":
    main()
