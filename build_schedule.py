#!/usr/bin/env python3
"""Genera media/ (JPEG y MP4 listos para la API) y schedule.json a partir de las
carpetas de historias-miami/programar/. Se ejecuta en la PC, una vez, o cada vez
que cambien las piezas."""
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.environ.get("KIT_DIR", os.path.join(HERE, "..", "historias-miami", "programar"))
FFMPEG = os.environ.get("FFMPEG", shutil.which("ffmpeg") or os.path.expandvars(
    r"%LOCALAPPDATA%\Microsoft\WinGet\Packages\Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe"
    r"\ffmpeg-9.0.2-full_build\bin\ffmpeg.exe"))
TZ = "-05:00"  # Ecuador, sin horario de verano
STORY_GAP_MIN = 2

FOLDER = re.compile(r"^(\d+)_(\d{4}-\d{2}-\d{2})_(\d{2})h(\d{2})_([A-Z]+)_(.+)$")


def ff(args):
    r = subprocess.run([FFMPEG, "-y", "-loglevel", "error", *args], capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit(f"ffmpeg falló: {r.stderr.strip()}")


def convert(src, dst):
    if src.lower().endswith(".mp4"):
        # La API de Reels es más estable con pista de audio: se agrega una silenciosa.
        ff(["-i", src, "-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=48000",
            "-shortest", "-c:v", "copy", "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", dst])
    else:
        # La API de Instagram solo acepta JPEG para imágenes.
        ff(["-i", src, "-q:v", "2", "-pix_fmt", "yuvj420p", dst])


def main():
    if not os.path.isdir(SRC):
        sys.exit(f"No existe la carpeta del kit: {SRC}")
    media_dir = os.path.join(HERE, "media")
    if os.path.isdir(media_dir):
        shutil.rmtree(media_dir)
    os.makedirs(media_dir)

    items = []
    for d in sorted(os.listdir(SRC)):
        m = FOLDER.match(d)
        if not m:
            continue
        num, date, hh, mm, kind, slug = m.groups()
        folder = os.path.join(SRC, d)
        files = sorted(f for f in os.listdir(folder) if f.lower().endswith((".png", ".jpg", ".mp4")))
        with open(os.path.join(folder, "texto.txt"), encoding="utf-8-sig") as fh:
            caption = fh.read().strip()

        media = []
        for i, f in enumerate(files, 1):
            ext = ".mp4" if f.lower().endswith(".mp4") else ".jpg"
            name = f"{num}_{i:02d}{ext}"
            convert(os.path.join(folder, f), os.path.join(media_dir, name))
            media.append(name)

        at = datetime.fromisoformat(f"{date}T{hh}:{mm}:00")
        base = {"id": f"{num}-{slug}", "at": at.isoformat() + TZ}
        if kind == "POST":
            items.append({**base, "type": "FEED", "media": media, "caption": caption})
        elif kind == "CARRUSEL":
            items.append({**base, "type": "CAROUSEL", "media": media, "caption": caption})
        elif kind == "REEL":
            items.append({**base, "type": "REEL", "media": media, "caption": caption})
        elif kind in ("HISTORIA", "HISTORIAS"):
            for i, mf in enumerate(media):
                t = at + timedelta(minutes=STORY_GAP_MIN * i)
                items.append({"id": f"{num}-{slug}-{i + 1}", "at": t.isoformat() + TZ,
                              "type": "STORY", "media": [mf]})
        else:
            sys.exit(f"Tipo desconocido en {d}: {kind}")

    items.sort(key=lambda x: x["at"])
    with open(os.path.join(HERE, "schedule.json"), "w", encoding="utf-8") as fh:
        json.dump({"paused": False, "items": items}, fh, ensure_ascii=False, indent=2)
    print(f"{len(items)} publicaciones en schedule.json, {len(os.listdir(media_dir))} archivos en media/")
    for it in items:
        print(f"  {it['at']}  {it['type']:<8} {it['id']}  ({len(it['media'])} archivo/s)")


if __name__ == "__main__":
    main()
