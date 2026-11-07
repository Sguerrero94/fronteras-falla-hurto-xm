# Fronteras comerciales en falla o hurto (XM, CREG 038 de 2014)

XM publica cada día un Excel con las fronteras comerciales que tienen una falla o un hurto abierto en su sistema de medida. Un archivo es una foto del día; juntándolos se ve la película.

Este proyecto descarga esas publicaciones, las carga a una base PostgreSQL (Supabase) y las analiza en un notebook.

## Qué contiene

| Archivo | Para qué sirve |
|---|---|
| `pipeline/descargar.py` | Revisa la biblioteca de archivos de XM y descarga solo lo que falta |
| `pipeline/cargar.py` | Carga cada Excel a la base, separando la falla de su estado diario |
| `pipeline/schema.sql` | Esquema `fronteras` en PostgreSQL |
| `pipeline/ejecutar.bat` | Ejecución diaria en Windows, con registro en `logs/` |
| `analisis_falla_hurto.ipynb` | Análisis exploratorio leído directo de la base |

## Cómo usarlo

```bash
cd pipeline
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
copy .env.example .env      # y pega tu cadena de conexión de Supabase
.venv\Scripts\python descargar.py --desde 2026-10-01
```

El archivo `.env` con la contraseña nunca se sube al repositorio.

## Hallazgos (corte al 6 de noviembre de 2026, 37 días, 59.124 registros)

- El 85 % de las fallas activas son de dispositivos de interfaz de comunicación.
- Un solo representante concentra el 81 % de las fallas activas y explica los saltos de la serie en octubre. En el análisis aparece como "Representante A".
- El 99 % de sus fallas de comunicación (1.260 de 1.271) salió exactamente el día 30, el último que permite el Anexo 7 de la CREG 038 (15 días más una ampliación de 15). En los demás representantes, la mediana fue de 16 días.

Los datos muestran el patrón, no la causa.

## Créditos

El modelo de datos (tablas, índices y las vistas `v_agente_actividad` y `v_falla_publicada`) es de **Juan Camilo Jaramillo Tascón**, que lo compartió como punto de partida. Sobre esa base solo se hicieron tres ajustes para Supabase:

- Las tablas van en el esquema `fronteras`, que Supabase no expone por su API pública.
- La tabla `publicacion` admite archivos consolidados con varias fechas (`nombre_archivo` deja de ser único y `modificado_en_xm` puede quedar vacío).
- `cargado_en` se guarda en hora de Colombia.

Datos: XM S.A. E.S.P., publicación diaria de fronteras en falla o hurto.

Autor: Santiago Guerrero Hernández, Ingeniero Electricista (UTP).
