#!/usr/bin/env python3
"""Carga a Supabase los archivos de fronteras en falla o hurto de XM.

Uso:
  python cargar.py --crear-esquema           crea las tablas (solo la primera vez)
  python cargar.py archivos/                 carga todos los .xlsx de la carpeta
  python cargar.py archivo.xlsx --forzar     recarga aunque el archivo ya esté

Cada archivo se carga en una sola transacción: si algo falla, no queda nada a
medias. Volver a cargar un archivo no duplica datos. Si una fecha ya existe, sus
observaciones se reemplazan por las del archivo nuevo.
"""
import argparse
import hashlib
import os
import re
import sys
from pathlib import Path

import pandas as pd
import psycopg

AQUI = Path(__file__).resolve().parent

COLUMNAS = {
    "Fecha de Publicación": "fecha_publicacion",
    "Código SIC": "codigo_sic",
    "Tipo de Frontera": "tipo_frontera",
    "Equipo en Falla": "equipo",
    "Fecha de Reporte de la Falla": "fecha_reporte",
    "Fecha Máxima de Normalización": "fecha_maxima",
    "Días en Falla": "dias",
    "Solicitó Ampliación de Plazo": "solicito",
    "Fecha de Solicitud de Ampliación": "fecha_solicitud",
    "Requerimientos": "requerimientos",
    "Agente que Reportó la Falla": "reporta",
    "Agente Representante": "representante",
    "Operador de Red": "operador",
}
OBLIGATORIAS = [c for c in COLUMNAS.values() if c not in ("fecha_solicitud", "requerimientos")]
STAGING = ["fecha_publicacion", "codigo_sic", "tipo_frontera", "equipo", "fecha_reporte",
           "fecha_maxima", "fecha_solicitud", "requerimientos",
           "reporta_rs", "reporta_ac", "representante_rs", "representante_ac",
           "operador_rs", "operador_ac"]


def url_base_datos():
    if os.environ.get("DATABASE_URL"):
        return os.environ["DATABASE_URL"]
    env = AQUI / ".env"
    if env.exists():
        for linea in env.read_text(encoding="utf-8").splitlines():
            if linea.startswith("DATABASE_URL="):
                return linea.split("=", 1)[1].strip()
    sys.exit("Falta DATABASE_URL: copia .env.example como .env y pega la cadena de Supabase.")


def conectar():
    # prepare_threshold=None evita errores con los poolers de Supabase.
    return psycopg.connect(url_base_datos(), prepare_threshold=None)


def leer(ruta):
    df = pd.read_excel(ruta, sheet_name=0, dtype={"Código SIC": str, "Requerimientos": str})
    if "Fecha de Publicación" not in df.columns:
        # El archivo diario de XM no trae esa columna: la fecha va en el nombre.
        fecha = re.search(r"\d{4}-\d{2}-\d{2}", ruta.name)
        if not fecha:
            raise ValueError("no se pudo sacar la fecha de publicación del nombre del archivo")
        df["Fecha de Publicación"] = fecha.group(0)
    faltan = [c for c in COLUMNAS if c not in df.columns]
    if faltan:
        raise ValueError(f"faltan columnas: {faltan}")
    df = df[list(COLUMNAS)].rename(columns=COLUMNAS)
    for col in ("fecha_publicacion", "fecha_maxima", "fecha_solicitud"):
        df[col] = pd.to_datetime(df[col].replace("", pd.NaT)).dt.date
    df["fecha_reporte"] = pd.to_datetime(df["fecha_reporte"])
    return df


def validar(df):
    """Detiene la carga si el archivo rompe algo que el esquema da por cierto."""
    errores = []
    nulos = df[OBLIGATORIAS].isna().sum()
    if nulos.any():
        errores.append(f"valores vacíos: {nulos[nulos > 0].to_dict()}")
    llave = ["fecha_publicacion", "codigo_sic", "equipo", "fecha_reporte"]
    if df.duplicated(llave).any():
        errores.append(f"{df.duplicated(llave).sum()} filas duplicadas")
    con_fecha = df["fecha_solicitud"].notna()
    if (con_fecha != df["solicito"].eq("Sí")).any():
        errores.append("'Solicitó Ampliación' no coincide con la fecha de solicitud")
    dias = (pd.to_datetime(df["fecha_publicacion"]) - df["fecha_reporte"].dt.normalize()).dt.days
    if (dias != df["dias"]).any():
        errores.append(f"{(dias != df['dias']).sum()} filas con 'Días en Falla' distinto al calculado")
    for col in ("reporta", "representante", "operador"):
        if not df[col].str.contains(" - ", regex=False).all():
            errores.append(f"{col}: hay agentes sin el formato 'RAZÓN SOCIAL - ACTIVIDAD'")
    return errores


def separar_agentes(df):
    for col in ("reporta", "representante", "operador"):
        # Se corta por el último " - " porque la razón social puede traer guiones.
        partes = df[col].str.rsplit(" - ", n=1, expand=True)
        df[f"{col}_rs"] = partes[0].str.strip()
        df[f"{col}_ac"] = partes[1].str.strip()
    return df


SQL_DIMENSIONES = """
INSERT INTO tipo_frontera (nombre)
 SELECT DISTINCT tipo_frontera FROM stg ON CONFLICT DO NOTHING;
INSERT INTO tipo_falla (nombre, categoria)
 SELECT DISTINCT equipo, CASE WHEN equipo LIKE 'Hurto%' THEN 'HURTO' ELSE 'FALLA' END
 FROM stg ON CONFLICT DO NOTHING;
INSERT INTO frontera (codigo_sic)
 SELECT DISTINCT codigo_sic FROM stg ON CONFLICT DO NOTHING;
CREATE TEMP TABLE stg_agente ON COMMIT DROP AS
 SELECT reporta_rs AS rs, reporta_ac AS ac FROM stg
 UNION SELECT representante_rs, representante_ac FROM stg
 UNION SELECT operador_rs, operador_ac FROM stg;
INSERT INTO agente (razon_social)
 SELECT DISTINCT rs FROM stg_agente ON CONFLICT DO NOTHING;
INSERT INTO actividad (nombre)
 SELECT DISTINCT ac FROM stg_agente ON CONFLICT DO NOTHING;
INSERT INTO agente_actividad (agente_id, actividad_id)
 SELECT a.agente_id, c.actividad_id FROM stg_agente s
 JOIN agente a ON a.razon_social = s.rs JOIN actividad c ON c.nombre = s.ac
 ON CONFLICT DO NOTHING;
CREATE TEMP TABLE mapa_agente ON COMMIT DROP AS
 SELECT a.razon_social AS rs, c.nombre AS ac, aa.agente_actividad_id AS id
 FROM agente_actividad aa JOIN agente a USING (agente_id) JOIN actividad c USING (actividad_id);
"""

SQL_HECHOS = """
INSERT INTO falla (frontera_id, tipo_falla_id, fecha_reporte, agente_reporta_id)
 SELECT DISTINCT ON (fr.frontera_id, tf.tipo_falla_id, s.fecha_reporte)
  fr.frontera_id, tf.tipo_falla_id, s.fecha_reporte, m.id
 FROM stg s
 JOIN frontera fr ON fr.codigo_sic = s.codigo_sic
 JOIN tipo_falla tf ON tf.nombre = s.equipo
 JOIN mapa_agente m ON m.rs = s.reporta_rs AND m.ac = s.reporta_ac
 ON CONFLICT DO NOTHING;
CREATE TEMP TABLE stg_falla ON COMMIT DROP AS
 SELECT s.*, p.publicacion_id, f.falla_id
 FROM stg s
 JOIN publicacion p ON p.fecha_publicacion = s.fecha_publicacion
 JOIN frontera fr ON fr.codigo_sic = s.codigo_sic
 JOIN tipo_falla tf ON tf.nombre = s.equipo
 JOIN falla f ON f.frontera_id = fr.frontera_id AND f.tipo_falla_id = tf.tipo_falla_id
  AND f.fecha_reporte = s.fecha_reporte;
INSERT INTO falla_observacion (publicacion_id, falla_id, fecha_maxima_normalizacion,
  fecha_solicitud_ampliacion, tipo_frontera_id, agente_representante_id, operador_red_id)
 SELECT s.publicacion_id, s.falla_id, s.fecha_maxima, s.fecha_solicitud,
  tfr.tipo_frontera_id, rep.id, op.id
 FROM stg_falla s
 JOIN tipo_frontera tfr ON tfr.nombre = s.tipo_frontera
 JOIN mapa_agente rep ON rep.rs = s.representante_rs AND rep.ac = s.representante_ac
 JOIN mapa_agente op ON op.rs = s.operador_rs AND op.ac = s.operador_ac;
CREATE TEMP TABLE stg_req ON COMMIT DROP AS
 SELECT DISTINCT publicacion_id, falla_id, btrim(n) AS numero
 FROM stg_falla, unnest(string_to_array(requerimientos, '|')) AS n
 WHERE btrim(n) <> '';
INSERT INTO requerimiento (numero)
 SELECT DISTINCT numero FROM stg_req ON CONFLICT DO NOTHING;
INSERT INTO falla_observacion_requerimiento (publicacion_id, falla_id, requerimiento_id)
 SELECT s.publicacion_id, s.falla_id, r.requerimiento_id
 FROM stg_req s JOIN requerimiento r ON r.numero = s.numero;
"""


def cargar_archivo(con, ruta, forzar=False, modificado=None):
    contenido = ruta.read_bytes()
    sha = hashlib.sha256(contenido).hexdigest()
    df = leer(ruta)
    errores = validar(df)
    if errores:
        raise ValueError("; ".join(errores))
    df = separar_agentes(df)
    fechas = sorted(df["fecha_publicacion"].unique())

    with con.transaction(), con.cursor() as cur:
        cur.execute("SET LOCAL search_path = fronteras")
        cur.execute("SELECT count(*) FROM publicacion WHERE sha256 = %s AND fecha_publicacion = ANY(%s)",
                    (sha, fechas))
        if cur.fetchone()[0] == len(fechas) and not forzar:
            if modificado:
                cur.execute("UPDATE publicacion SET modificado_en_xm = %s WHERE fecha_publicacion = ANY(%s)",
                            (modificado, fechas))
            print(f"  {ruta.name}: ya estaba cargado, se omite")
            return
        cur.execute("""CREATE TEMP TABLE stg (fecha_publicacion date, codigo_sic text,
            tipo_frontera text, equipo text, fecha_reporte timestamp, fecha_maxima date,
            fecha_solicitud date, requerimientos text, reporta_rs text, reporta_ac text,
            representante_rs text, representante_ac text, operador_rs text, operador_ac text)
            ON COMMIT DROP""")
        filas = df[STAGING].astype(object).where(df[STAGING].notna(), None)
        with cur.copy(f"COPY stg ({', '.join(STAGING)}) FROM STDIN") as copia:
            for fila in filas.itertuples(index=False, name=None):
                copia.write_row(fila)
        cur.execute(SQL_DIMENSIONES)
        cur.execute("""DELETE FROM falla_observacion_requerimiento WHERE publicacion_id IN
            (SELECT publicacion_id FROM publicacion WHERE fecha_publicacion = ANY(%s))""", (fechas,))
        cur.execute("""DELETE FROM falla_observacion WHERE publicacion_id IN
            (SELECT publicacion_id FROM publicacion WHERE fecha_publicacion = ANY(%s))""", (fechas,))
        cur.execute("""INSERT INTO publicacion
             (fecha_publicacion, nombre_archivo, bytes, sha256, modificado_en_xm)
            SELECT unnest(%s::date[]), %s, %s, %s, %s
            ON CONFLICT (fecha_publicacion) DO UPDATE SET nombre_archivo = EXCLUDED.nombre_archivo,
             bytes = EXCLUDED.bytes, sha256 = EXCLUDED.sha256,
             modificado_en_xm = EXCLUDED.modificado_en_xm,
             cargado_en = now() AT TIME ZONE INTERVAL '-05:00'""",
                    (fechas, ruta.name, len(contenido), sha, modificado))
        cur.execute(SQL_HECHOS)
    print(f"  {ruta.name}: {len(df):,} filas, {len(fechas)} fechas ({fechas[0]} a {fechas[-1]})")


def reportar(con):
    with con.transaction(), con.cursor() as cur:
        cur.execute("SET LOCAL search_path = fronteras")
        cur.execute("SELECT count(*), min(fecha_publicacion), max(fecha_publicacion) FROM publicacion")
        n, ini, fin = cur.fetchone()
        if not n:
            return
        cur.execute("SELECT count(*) FROM falla_observacion")
        obs = cur.fetchone()[0]
        cur.execute("SELECT count(*) FROM falla")
        fallas = cur.fetchone()[0]
        print(f"En la base: {n} publicaciones ({ini} a {fin}), {fallas:,} fallas, {obs:,} observaciones")
        cur.execute("""SELECT d::date FROM generate_series(%s::date, %s::date, '1 day') d
            WHERE d::date NOT IN (SELECT fecha_publicacion FROM publicacion) ORDER BY 1""", (ini, fin))
        huecos = [str(f[0]) for f in cur.fetchall()]
        if huecos:
            print(f"AVISO: faltan {len(huecos)} días. Una falla no debe darse por cerrada en esos días: {huecos[:10]}")


def main():
    ap = argparse.ArgumentParser(description="Carga a Supabase las fronteras en falla o hurto de XM.")
    ap.add_argument("rutas", nargs="*", type=Path, help="archivos .xlsx o carpetas")
    ap.add_argument("--crear-esquema", action="store_true", help="crea las tablas")
    ap.add_argument("--forzar", action="store_true", help="recarga archivos ya cargados")
    args = ap.parse_args()

    archivos = []
    for ruta in args.rutas:
        if ruta.is_dir():
            archivos += sorted(p for p in ruta.glob("*.xlsx") if not p.name.startswith("~$"))
        else:
            archivos.append(ruta)

    fallidos = 0
    with conectar() as con:
        if args.crear_esquema:
            with con.transaction(), con.cursor() as cur:
                cur.execute((AQUI / "schema.sql").read_text(encoding="utf-8"))
            print("Esquema creado.")
        for ruta in archivos:
            try:
                cargar_archivo(con, ruta, args.forzar)
            except (ValueError, psycopg.Error) as error:
                fallidos += 1
                print(f"  ERROR en {ruta.name}: {error}", file=sys.stderr)
        reportar(con)
    sys.exit(1 if fallidos else 0)


if __name__ == "__main__":
    main()
