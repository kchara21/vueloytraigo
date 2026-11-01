# Vuelo y Traigo · publicación automática

GitHub Actions ejecuta `publish.py` cada 10 minutos. Publica en Instagram lo que
`schedule.json` marca para esa hora (hora de Ecuador) y guarda el resultado en
`state.json`. Las imágenes se sirven desde GitHub Pages porque la API de
Instagram solo acepta archivos por URL pública.

## Lo que hace y lo que no

| Sí | No (límite de la API de Instagram) |
|---|---|
| Publicaciones, carruseles, historias y reels a la hora programada | Cambiar la foto de perfil o la biografía |
| Reintentar hasta 3 veces si algo falla | Stickers en historias (encuestas, enlaces, cuenta regresiva) |
| Omitir lo que tenga más de 12 horas de atraso | Música de la biblioteca de Instagram en reels |

## Comandos

```
gh workflow run autopost -f mode=check     # verifica el token
gh workflow run autopost -f mode=dry-run   # qué se publicaría ahora
gh workflow disable autopost               # pausa todo
gh workflow enable autopost                # reanuda
```

Para cambiar el calendario: edita las carpetas de `historias-miami/programar/`,
ejecuta `python build_schedule.py` y sube los cambios.

El token de Instagram dura 60 días. Para esta campaña (3 nov a 14 dic) alcanza.
