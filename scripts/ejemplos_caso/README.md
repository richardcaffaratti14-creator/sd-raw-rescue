# Ejemplos del caso de referencia

Son los scripts a medida con los que se resolvió la tarjeta de referencia (`../../references/caso-referencia.md`), tal como se usaron. **Tienen rutas (`D:\RESCATE_SD\...`), números de cluster y nombres de foto de esa tarjeta: no se corren tal cual.** Se leen, se copia el patrón y se adaptan. Importan `rescatar_sd` y `sintesis` de la carpeta de arriba.

`TRABAJO` es una carpeta para intermedios (`.npz`, `.npy`, `.json`) que en el caso era el directorio temporal de la sesión: reemplazarla por una carpeta propia.

| Script | Qué muestra |
|---|---|
| `buildfat.py` | Fusionar la FAT1 real con las páginas de FAT de los volcados (base de cada página según su primer valor, coherencia entre copias) y seguir las cadenas esperadas. |
| `dumpdir.py` | Leer y mostrar las entradas de directorio 8.3 de cada volcado (nombre, cluster, tamaño, fecha). |
| `unswap.py`, `pieces.py` | Analizar las unidades de escritura: costo de empalme entre unidad lógica y física; trozos entre saltos fuertes y su mejor sucesor. |
| `flujo.py` | Armar el flujo lógico (grilla de unidades, volcados que acortan la unidad, pares intercambiados) y hacer la **prueba del largo** de cada foto. Usa el `.npz` de `mapa_clusters.py --out`. |
| `reconstruir.py` | Con el flujo, armar cada foto en bruto desde sus segmentos físicos y guardar un índice. |
| `entregar_intactas.py` | Sacar las fotos que no necesitan reparación: directorio de los volcados + FAT fusionada + des-intercambio; verificación (cabecera, filas, saltos con `raw_cost_profile`, fin de tira + 1024 = tamaño) y fecha original. |
| `reparar.py`, `reparar4.py`, `reparar5.py` | Rellenar huecos con `sintesis.fill_rows`: hueco entre dos tramos buenos; hueco grande y foto truncada; re-síntesis idempotente con método automático y copia de respaldo. |
| `borradas.py` | Fotos borradas (contiguas) y una parcial: cabecera donante, tira real, filas negras arriba, `so`/`sc` en el SubIFD y vista previa propia revelada con LibRaw. |
| `nef.py` | Extraer NEF contiguos desde sus cabeceras y verificarlos con `rawpy`. (La versión general es `../carve_cabeceras.py`.) |
| `orphans.py` | Cobertura: trozos RAW que ningún archivo conocido reclama. |
| `armar_final.py`, `fix_reporte.py`, `jpg_final.py`, `leeme_final.py` | Empaquetado final: carpeta, estados y notas, JPG de cámara o generados, LEEME y SHA-256. El LEEME de ejemplo tiene el evento como marcador `<NOMBRE DEL EVENTO>`. |
