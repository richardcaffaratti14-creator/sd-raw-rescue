# Disco NTFS formateado (disco externo, "lo formatearon sin querer")

Un formato rápido no borra los datos: escribe estructuras nuevas (en el caso real, 134 MB) y declara libre todo el resto. Si antes también era NTFS, la MFT nueva suele caer en el mismo lugar que la vieja y **solo pisa sus primeros registros**: el resto conserva nombre, carpeta, tamaño, fecha y la ubicación de cada archivo. Con eso se recupera mucho mejor que por firmas (que pierde nombres y carpetas).

## Reglas propias de este caso

- **Nunca guardar en el mismo disco.** Si el usuario lo pregunta: para el sistema nuevo todo es "libre", incluidos los lugares donde siguen los archivos; no se puede elegir dónde cae lo nuevo y cada copia modifica el índice. Repartirlo o reservar "el final" exige cambiar particiones, que también escribe.
- Que no acepte "formatear", "reparar" ni "analizar", ni que lo optimice. Revisar TRIM (`fsutil behavior query DisableDeleteNotify`) y el tipo de disco: en un SSD con TRIM los datos pueden haber quedado en cero. Lo contesta el muestreo, no la suposición.
- Una carpeta `.fseventsd` indica que pasó por una Mac: el sistema anterior puede no ser NTFS. El diagnóstico busca HFS+, APFS, exFAT y FAT32.

## Copia directa: la excepción a "imagen primero"

Una imagen completa de un disco de 1 TB necesita 1 TB, más lo que ocupe lo recuperado. Si el disco está **sano** (lecturas sin errores en el muestreo) y no hay lugar, se copia directo del volumen en solo lectura, en el orden del disco. **Decírselo al usuario**: no habrá respaldo de la imagen, así que el disco no se formatea ni se reusa hasta que revise. Si hay sectores ilegibles o el disco hace ruidos, no: primero la imagen.

## Flujo

1. `diagnostico F:` (elevado). Mirar: % con datos y en qué zonas, otros sistemas de archivos, y cuántos registros NTFS con nombre hay. Si no hay registros, esta vía no sirve: recuperar por firmas sobre una imagen.
2. `rescatar_sd.py image F: --size 4294967296 --out RUN\imagen\inicio.img` (elevado, medio minuto). La MFT suele empezar a los 3 GiB del volumen (LCN 786432 con clusters de 4 KB).
3. `inventario inicio.img RUN`: lista todo **sin necesitar lugar**. Mostrarle al usuario carpetas y tamaños (muchas veces no sabe qué hay). Si avisa que hay registros pegados al final de la copia, copiar más y repetir. La suma de tamaños y el byte donde termina el último dato tienen que ser coherentes con el muestreo.
4. **Espacio**: con `copiar --simular` ver cuánto ocupa cada carpeta. Windows muestra GiB (los GB del Explorador); el inventario también informa GB decimales. Decir cuál se está usando. Si no entra todo, por tandas con `--sin` / `--solo`; el copiador para antes de llenar el disco (`--reserva-gib`) y se retoma.
5. `copiar F: RUN --tanda tanda1` (elevado). Verifica que la serie NTFS sea la del inventario y que el destino no sea el origen. A los pocos minutos, **abrir una muestra de lo copiado**: una cabecera correcta no alcanza.
6. `verificar RUN tanda1` y al final `entrega RUN`. `entrega` informa cuántos archivos del índice quedaron sin copiar: tiene que ser 0 antes de dar por terminado.

Para lanzar los pasos elevados, la receta de `SKILL.md`; si el usuario cancela el cartel o vence, `Start-Process` falla con "El usuario ha cancelado la operación": no relanzar sin avisar. Para esperar una copia larga, dejar en segundo plano una consulta que termine cuando el log diga `== FIN` o deje de avanzar.

## Qué pisa el formateo

`inventario` lee el `$Bitmap` del sistema **nuevo** (registro 6) y cuenta, por archivo, cuántos de sus clusters ocupa hoy. Es una prueba positiva de daño, no una garantía de sanidad: algo escrito y liberado después (otra computadora, índices temporales) no figura. En el caso real, 6 de 30 821 archivos estaban pisados: 3 quedaron ilegibles, 2 se leían igual y 1 era un log.

## Límites

- Archivos comprimidos o cifrados por NTFS salen como están en el disco (no abren): el inventario los cuenta.
- Una carpeta cuyo registro se pisó queda como `CARPETA_SIN_NOMBRE_n`, con lo de adentro intacto.
- Si el sistema anterior era HFS+, APFS o exFAT, esta herramienta no sirve para sus nombres.
- RAW: sin LibRaw se comprueba estructura, huecos y la imagen embebida, no el revelado. Decirlo.

## Caso de referencia (anonimizado)

Disco USB de 1 TB, NTFS formateado sobre NTFS. Muestreo: 56 % con datos, todo al principio. Inventario desde 4 GB: 30 821 archivos, 558 GB, ninguno fragmentado. Copia directa en dos tandas: 30 811 archivos (los 10 restantes eran del sistema) a unos 121-132 MB/s, sin sectores ilegibles. Revisión: todas las fotos y videos pasaron; 3 XML dañados y 2 a revisar.
