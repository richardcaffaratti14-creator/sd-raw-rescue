# sd-raw-rescue

**Skill para Claude Code que rescata fotos RAW de tarjetas SD/CF dañadas, en solo lectura.** Nació de una recuperación real que salió mejor que PhotoRec y TestDisk: reconstruye el directorio y la FAT desde los volcados de metadatos que quedan en la tarjeta, reordena las unidades de escritura intercambiadas, saca fotos borradas por cabecera TIFF y repara filas faltantes de un ARW de Sony usando el JPEG que la cámara guarda dentro del RAW.

**English:** a Claude Code skill plus Python tools to recover RAW photos (Sony ARW, Nikon NEF, Canon CR2…) from damaged, deleted or corrupted SD cards — strictly read-only, working on a 1:1 image of the card. It rebuilds directory and FAT from stray metadata dumps, re-orders swapped write units, carves deleted photos by TIFF header, and repairs missing rows of Sony ARW2 files from the embedded JPEG. Documentation is in Spanish.

## Instalar

```powershell
git clone https://github.com/richardcaffaratti14-creator/sd-raw-rescue "$env:USERPROFILE\.claude\skills\sd-raw-rescue"
pip install numpy pillow rawpy
```

Después, en Claude Code, basta con contar el problema ("se rompieron las fotos de mi tarjeta SD, ayudame a recuperarlas"): la skill se activa sola.

## Qué hay

- `SKILL.md`: reglas, flujo en fases, receta para leer la tarjeta cruda en Windows y trampas conocidas.
- `references/`: método forense, formato ARW2 de Sony, armado de la entrega y el caso de referencia con cifras.
- `scripts/`: `rescatar_sd.py` (herramienta general, solo biblioteca estándar) y las herramientas de diagnóstico, rescate y reparación; `RESCATAR_TARJETA.bat` para quien prefiere un menú; `ejemplos_caso/` con los scripts reales del caso.

## Alcance y avisos

- Probado con una tarjeta FAT32 de 32 GB con fotos ARW de una Sony A7R V y NEF de una Nikon D7200. La síntesis de filas es solo para ARW2 y tiene las constantes de la A7R V; para otro modelo hay que ajustarlas (ver `references/arw2-sony.md`).
- FAT32 completo; exFAT (tarjetas de 64 GB o más) solo con `carve_cabeceras.py` y `mapa_clusters.py` (sin directorio ni FAT; probado nada más que con una imagen sintética).
- Las filas rehechas tienen calidad JPEG, no de RAW: la skill obliga a rotularlas.
- Trabajar siempre sobre una imagen de la tarjeta. Sin garantía: las herramientas se hicieron para un caso real y no reemplazan un servicio profesional de recuperación si la tarjeta tiene daño físico.
