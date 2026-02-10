-- MySQL seed: calendario_inhabil (México 2026)
-- Target DB: moving_productiva (ejecutar con USE moving_productiva; antes del script)

INSERT INTO calendario_inhabil (fecha, es_oficial, descripcion, fuente, activo, created_by)
VALUES
('2026-01-01', 1, 'Año Nuevo', 'Art. 74 LFT / PROFEDET', 1, 'TI'),
('2026-02-02', 1, 'Conmemoración Constitución (primer lunes de febrero)', 'Art. 74 LFT / PROFEDET', 1, 'TI'),
('2026-03-16', 1, 'Conmemoración Natalicio Benito Juárez (tercer lunes de marzo)', 'Art. 74 LFT / PROFEDET', 1, 'TI'),
('2026-05-01', 1, 'Día del Trabajo', 'Art. 74 LFT / PROFEDET', 1, 'TI'),
('2026-09-16', 1, 'Independencia', 'Art. 74 LFT / PROFEDET', 1, 'TI'),
('2026-11-16', 1, 'Conmemoración Revolución (tercer lunes de noviembre)', 'Art. 74 LFT / PROFEDET', 1, 'TI'),
('2026-12-25', 1, 'Navidad', 'Art. 74 LFT / PROFEDET', 1, 'TI')
ON DUPLICATE KEY UPDATE
  es_oficial = VALUES(es_oficial),
  descripcion = VALUES(descripcion),
  fuente = VALUES(fuente),
  activo = VALUES(activo),
  updated_by = 'TI',
  updated_at = CURRENT_TIMESTAMP;
