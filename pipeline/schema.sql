-- Fronteras comerciales en falla o hurto (XM, Res. CREG 038 de 2014).
-- Para Supabase (PostgreSQL). Las tablas van en el esquema "fronteras", que
-- Supabase no expone por su API pública; solo se accede con la conexión directa.
-- Las fechas se guardan tal como las publica XM, en hora de Colombia.

CREATE SCHEMA fronteras;
SET LOCAL search_path = fronteras;

-- Catálogos

CREATE TABLE tipo_frontera (
 tipo_frontera_id INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
 nombre VARCHAR(50) NOT NULL UNIQUE
);

CREATE TABLE tipo_falla (
 tipo_falla_id INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
 nombre VARCHAR(100) NOT NULL UNIQUE,
 categoria VARCHAR(10) NOT NULL CHECK (categoria IN ('FALLA', 'HURTO'))
);

CREATE TABLE actividad (
 actividad_id INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
 nombre VARCHAR(30) NOT NULL UNIQUE
);

CREATE TABLE agente (
 agente_id INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
 razon_social VARCHAR(120) NOT NULL UNIQUE
);

-- Una empresa puede actuar con varias actividades.
CREATE TABLE agente_actividad (
 agente_actividad_id INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
 agente_id INTEGER NOT NULL REFERENCES agente (agente_id),
 actividad_id INTEGER NOT NULL REFERENCES actividad (actividad_id),
 UNIQUE (agente_id, actividad_id)
);

CREATE TABLE frontera (
 frontera_id INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
 codigo_sic VARCHAR(20) NOT NULL UNIQUE
);

CREATE TABLE requerimiento (
 requerimiento_id INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
 numero VARCHAR(30) NOT NULL UNIQUE
);

-- Una fila por fecha publicada. El archivo de origen puede ser el diario de XM
-- o un consolidado con varias fechas, por eso nombre_archivo y sha256 pueden
-- repetirse. modificado_en_xm queda vacío si no se conoce.
CREATE TABLE publicacion (
 publicacion_id INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
 fecha_publicacion DATE NOT NULL UNIQUE,
 nombre_archivo VARCHAR(100) NOT NULL,
 bytes INTEGER NOT NULL CHECK (bytes > 0),
 sha256 CHAR(64) NOT NULL,
 modificado_en_xm TIMESTAMP,
 cargado_en TIMESTAMP NOT NULL DEFAULT (now() AT TIME ZONE INTERVAL '-05:00')
);

-- Evento de falla: lo que no cambia mientras sigue abierto.
CREATE TABLE falla (
 falla_id INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
 frontera_id INTEGER NOT NULL REFERENCES frontera (frontera_id),
 tipo_falla_id INTEGER NOT NULL REFERENCES tipo_falla (tipo_falla_id),
 fecha_reporte TIMESTAMP NOT NULL,
 agente_reporta_id INTEGER NOT NULL REFERENCES agente_actividad (agente_actividad_id),
 UNIQUE (frontera_id, tipo_falla_id, fecha_reporte)
);

-- Estado de cada falla en cada publicación diaria.
-- El tipo de frontera, el representante y el operador de red van aquí para
-- conservar su historia si XM los cambia.
CREATE TABLE falla_observacion (
 publicacion_id INTEGER NOT NULL REFERENCES publicacion (publicacion_id),
 falla_id INTEGER NOT NULL REFERENCES falla (falla_id),
 fecha_maxima_normalizacion DATE NOT NULL,
 fecha_solicitud_ampliacion DATE,
 tipo_frontera_id INTEGER NOT NULL REFERENCES tipo_frontera (tipo_frontera_id),
 agente_representante_id INTEGER NOT NULL REFERENCES agente_actividad (agente_actividad_id),
 operador_red_id INTEGER NOT NULL REFERENCES agente_actividad (agente_actividad_id),
 PRIMARY KEY (publicacion_id, falla_id)
);

CREATE TABLE falla_observacion_requerimiento (
 publicacion_id INTEGER NOT NULL,
 falla_id INTEGER NOT NULL,
 requerimiento_id INTEGER NOT NULL REFERENCES requerimiento (requerimiento_id),
 PRIMARY KEY (publicacion_id, falla_id, requerimiento_id),
 FOREIGN KEY (publicacion_id, falla_id)
 REFERENCES falla_observacion (publicacion_id, falla_id)
);

CREATE INDEX ix_agente_actividad_actividad ON agente_actividad (actividad_id);
CREATE INDEX ix_falla_tipo_falla ON falla (tipo_falla_id);
CREATE INDEX ix_falla_agente_reporta ON falla (agente_reporta_id);
CREATE INDEX ix_observacion_falla ON falla_observacion (falla_id);
CREATE INDEX ix_observacion_tipo_frontera ON falla_observacion (tipo_frontera_id);
CREATE INDEX ix_observacion_representante ON falla_observacion (agente_representante_id);
CREATE INDEX ix_observacion_operador_red ON falla_observacion (operador_red_id);
CREATE INDEX ix_obs_req_requerimiento ON falla_observacion_requerimiento (requerimiento_id);

-- Agente con el formato de XM: «RAZÓN SOCIAL - ACTIVIDAD».
CREATE VIEW v_agente_actividad AS
SELECT aa.agente_actividad_id,
 a.razon_social || ' - ' || ac.nombre AS nombre_xm
FROM agente_actividad aa
JOIN agente a ON a.agente_id = aa.agente_id
JOIN actividad ac ON ac.actividad_id = aa.actividad_id;

-- Una fila por falla y publicación, con los datos derivables calculados.
-- Sin la columna Requerimientos, que está en falla_observacion_requerimiento.
CREATE VIEW v_falla_publicada AS
SELECT p.fecha_publicacion,
 fr.codigo_sic,
 tfr.nombre AS tipo_frontera,
 tf.nombre AS equipo_en_falla,
 tf.categoria,
 f.fecha_reporte,
 o.fecha_maxima_normalizacion,
 p.fecha_publicacion - CAST(f.fecha_reporte AS DATE) AS dias_en_falla,
 CASE WHEN o.fecha_solicitud_ampliacion IS NULL THEN 'No' ELSE 'Sí' END
 AS solicito_ampliacion,
 o.fecha_solicitud_ampliacion,
 reporta.nombre_xm AS agente_reporta,
 repres.nombre_xm AS agente_representante,
 opera.nombre_xm AS operador_red
FROM falla_observacion o
JOIN publicacion p ON p.publicacion_id = o.publicacion_id
JOIN falla f ON f.falla_id = o.falla_id
JOIN frontera fr ON fr.frontera_id = f.frontera_id
JOIN tipo_falla tf ON tf.tipo_falla_id = f.tipo_falla_id
JOIN tipo_frontera tfr ON tfr.tipo_frontera_id = o.tipo_frontera_id
JOIN v_agente_actividad reporta ON reporta.agente_actividad_id = f.agente_reporta_id
JOIN v_agente_actividad repres ON repres.agente_actividad_id = o.agente_representante_id
JOIN v_agente_actividad opera ON opera.agente_actividad_id = o.operador_red_id;
