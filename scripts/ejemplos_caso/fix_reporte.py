"""Corrige estado/detalle del reporte final (claves por numero de foto - 1000) sin rehacer la carpeta.   (venv)"""
import csv
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
import armar_final as A

BASE = r'D:\RESCATE_SD'
p = os.path.join(BASE, 'logs', 'reporte_final.json')
rep = json.load(open(p, encoding='utf-8'))
cuenta = {}
for r in rep:
    if not r['archivo'].startswith('DSC'):
        continue
    k = int(r['archivo'][3:8]) - 1000
    estado, nota = A.NOTAS.get(k, ('COMPLETA', 'Recuperada entera y verificada (sin filas rehechas).'))
    base = r['fecha_camara'].replace(' (estimada)', '')
    r.update(estado=estado, detalle=nota, filas_rehechas=A.FILAS_SINT.get(k, 0),
             pct_rehecho=round(100.0 * A.FILAS_SINT.get(k, 0) / 6376, 1),
             nombre='deducido por la numeracion' if k in A.NOMBRE_DEDUCIDO else 'original',
             fecha_camara=base + (' (estimada)' if k in A.FECHA_ESTIMADA else ''))
    cuenta[estado] = cuenta.get(estado, 0) + 1
json.dump(rep, open(p, 'w'), indent=1, ensure_ascii=False)
with open(os.path.join(A.FINAL, 'REPORTE.csv'), 'w', newline='', encoding='utf-8-sig') as f:
    w = csv.DictWriter(f, fieldnames=list(rep[0].keys()), delimiter=';')
    w.writeheader()
    w.writerows(rep)
print(cuenta)
