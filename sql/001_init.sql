-- Create settings table
CREATE TABLE IF NOT EXISTS dashboard_settings (
  id INT PRIMARY KEY AUTO_INCREMENT,
  month DATE NOT NULL,
  business_days INT NOT NULL,
  installed_capacity_surtido INT NULL,
  installed_capacity_verificacion INT NULL,
  installed_capacity_embarques INT NULL,
  unit ENUM('pedidos','partidas') DEFAULT 'pedidos',
  holidays JSON NULL,
  thresholds JSON NULL,
  UNIQUE KEY uniq_month (month)
);

-- View normalized from Moving schema (keep 7 = NO PLANEAR as '7 NO')
DROP VIEW IF EXISTS vw_tablero_dash;
CREATE VIEW vw_tablero_dash AS
SELECT
  rp.pedido           AS pedido_cco,
  rp.fecha            AS fecha_alta,
  rp.ejecutivo        AS ejecutiva,
  rp.ruta_tipo        AS tipo,
  rp.sucursal         AS sucursal,
  rp.num_part         AS part_cco,
  rp.comentario       AS comentario_cco,
  rp.ent_cco          AS entrega_cco,
  rp.ent_fec_cco      AS fecha_surtido,
  rp.verificador      AS verificador,
  NULL                AS fecha_verificacion,
  rp.transportista    AS transportista,
  rp.unidad           AS unidad,
  rp.fecha_salida     AS fecha_embarque,
  CASE rp.proceso
    WHEN 1 THEN '1 EMB'
    WHEN 2 THEN '2 PLA'
    WHEN 3 THEN '3 SUR'
    WHEN 4 THEN '4 VER'
    WHEN 5 THEN '5 CER'
    WHEN 6 THEN '0 CAN'
    WHEN 7 THEN '7 NO'
    ELSE CAST(rp.proceso AS CHAR)
  END                 AS estado
FROM moving.registro_pedidos rp;

-- Example: seed September 2025 settings (adjust values)
INSERT INTO dashboard_settings (month, business_days, installed_capacity_surtido, installed_capacity_verificacion, installed_capacity_embarques, unit, holidays, thresholds)
VALUES ('2025-09-01', 22, 120, 115, 110, 'pedidos', JSON_ARRAY('2025-09-16'), JSON_OBJECT('rojo',0.8,'amarillo',0.9))
ON DUPLICATE KEY UPDATE business_days=VALUES(business_days);
