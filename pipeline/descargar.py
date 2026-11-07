#!/usr/bin/env python3
"""Descarga de XM los archivos diarios de fronteras en falla o hurto y los carga a Supabase.

Uso:
  python descargar.py                        revisa los últimos 35 días
  python descargar.py --desde 2026-10-01     revisa desde esa fecha
  python descargar.py --solo-listar          muestra qué haría, sin descargar

Solo descarga lo que falta en la base o lo que XM volvió a publicar. Por eso se
puede ejecutar todos los días, y un día sin ejecutar se recupera en el siguiente.
Usa la misma API pública de la biblioteca de archivos de la página de XM. XM no la
documenta para uso automático, así que puede cambiar: si cambia, el programa
termina con error y lo deja en el registro.
"""
import argparse
import base64
import re
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import requests

import cargar

API = "https://api-portalxm.xm.com.co/v2/administracion-archivos/ficheros"
CONTENEDOR = "storageportalxm"
RUTA = ("/M:/InformacionAgentes/Usuarios/PublicoK/SIC/COMERCIA/"
        "Fronteras_en_proceso_de_registro/PUB_Falla_Hurto")
ENCABEZADOS = {"Content-Type": "application/json",
               "Origin": "https://www.xm.com.co", "Referer": "https://www.xm.com.co/"}
NOMBRE = re.compile(r"PubFC_Falla-Hurto(\d{4}-\d{2}-\d{2})\.xlsx")
POR_PAGINA = 1000
PAUSA_SEGUNDOS = 1
CARPETA = cargar.AQUI / "archivos"


def b64(texto):
    return base64.urlsafe_b64encode(texto.encode()).decode().rstrip("=")


def listar(desde):
    """Archivos de XM desde esa fecha, del más antiguo al más nuevo."""
    archivos, pagina = [], 1
    while True:
        r = requests.post(f"{API}/consulta", headers=ENCABEZADOS, timeout=60, json={
            "ruta": b64(RUTA), "nombre": b64(""), "contenedor": CONTENEDOR,
            "ordenarPor": "nombre", "orden": "DESC",
            "pagina": pagina, "resultadosPorPagina": POR_PAGINA})
        r.raise_for_status()
        ficheros = r.json()["ficheros"]
        for f in ficheros:
            m = NOMBRE.fullmatch(f["nombre"])
            if m and date.fromisoformat(m.group(1)) >= desde:
                # La hora de XM viene en hora de Colombia, igual que el resto de la base.
                f["modificado"] = datetime.fromisoformat(f["fechaModificacion"]).replace(tzinfo=None)
                f["fecha"] = date.fromisoformat(m.group(1))
                archivos.append(f)
        # Van en orden descendente: al pasar de la fecha ya no hay más que revisar.
        nombres = [NOMBRE.fullmatch(f["nombre"]) for f in ficheros]
        fechas = [date.fromisoformat(m.group(1)) for m in nombres if m]
        if len(ficheros) < POR_PAGINA or (fechas and min(fechas) < desde):
            return sorted(archivos, key=lambda f: f["fecha"])
        pagina += 1
        time.sleep(PAUSA_SEGUNDOS)


def en_base(con, desde):
    with con.transaction(), con.cursor() as cur:
        cur.execute("""SELECT fecha_publicacion, modificado_en_xm FROM fronteras.publicacion
            WHERE fecha_publicacion >= %s""", (desde,))
        return dict(cur.fetchall())


def descargar(f):
    ruta_xm = f"{f['rutaAbsolutaPadre']}/{f['nombre']}"[1:]
    r = requests.post(f"{API}/descarga-archivo", headers=ENCABEZADOS, timeout=120,
                      json={"ruta": b64(ruta_xm), "nombreBlobContainer": CONTENEDOR})
    r.raise_for_status()
    if r.content[:2] != b"PK" or len(r.content) != f["tamanio"]:
        raise ValueError(f"descarga incompleta: {len(r.content)} bytes de {f['tamanio']}")
    CARPETA.mkdir(exist_ok=True)
    destino = CARPETA / f["nombre"]
    destino.write_bytes(r.content)
    return destino


def main():
    ap = argparse.ArgumentParser(description="Descarga y carga las fronteras en falla o hurto de XM.")
    ap.add_argument("--desde", type=date.fromisoformat, default=date.today() - timedelta(days=35))
    ap.add_argument("--solo-listar", action="store_true")
    args = ap.parse_args()

    print(f"{datetime.now():%Y-%m-%d %H:%M} Revisando XM desde {args.desde}")
    fallidos = 0
    with cargar.conectar() as con:
        archivos = listar(args.desde)
        cargadas = en_base(con, args.desde)
        pendientes = [f for f in archivos
                      if f["fecha"] not in cargadas or cargadas[f["fecha"]] != f["modificado"]]
        print(f"  {len(archivos)} archivos en XM, {len(pendientes)} por cargar")
        if args.solo_listar:
            for f in pendientes:
                print(f"  {f['nombre']}")
            return
        for f in pendientes:
            try:
                cargar.cargar_archivo(con, descargar(f), modificado=f["modificado"])
            except (ValueError, requests.RequestException, cargar.psycopg.Error) as error:
                fallidos += 1
                print(f"  ERROR en {f['nombre']}: {error}", file=sys.stderr)
            time.sleep(PAUSA_SEGUNDOS)
        cargar.reportar(con)
    sys.exit(1 if fallidos else 0)


if __name__ == "__main__":
    main()
