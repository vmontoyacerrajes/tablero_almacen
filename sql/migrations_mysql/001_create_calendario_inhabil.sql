-- MySQL migration: create calendario_inhabil
-- Target DB: moving_productiva (ejecutar con USE moving_productiva; antes del script)

CREATE TABLE IF NOT EXISTS calendario_inhabil (
  fecha        DATE NOT NULL,
  es_oficial   TINYINT(1) NOT NULL DEFAULT 1,
  descripcion  VARCHAR(200) NULL,
  fuente       VARCHAR(200) NULL,
  activo       TINYINT(1) NOT NULL DEFAULT 1,
  created_at   DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  created_by   VARCHAR(100) NULL,
  updated_at   DATETIME NULL DEFAULT NULL ON UPDATE CURRENT_TIMESTAMP,
  updated_by   VARCHAR(100) NULL,
  PRIMARY KEY (fecha),
  KEY idx_activo_fecha (activo, fecha)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
