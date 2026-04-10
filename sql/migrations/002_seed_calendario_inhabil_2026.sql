MERGE dbo.calendario_inhabil AS tgt
USING (VALUES
    (CONVERT(date,'2026-01-01'), 1, N'Año Nuevo', N'Art. 74 LFT / PROFEDET', 1),
    (CONVERT(date,'2026-02-02'), 1, N'Conmemoración Constitución (primer lunes de febrero)', N'Art. 74 LFT / PROFEDET', 1),
    (CONVERT(date,'2026-03-16'), 1, N'Conmemoración Natalicio Benito Juárez (tercer lunes de marzo)', N'Art. 74 LFT / PROFEDET', 1),
    (CONVERT(date,'2026-05-01'), 1, N'Día del Trabajo', N'Art. 74 LFT / PROFEDET', 1),
    (CONVERT(date,'2026-09-16'), 1, N'Independencia', N'Art. 74 LFT / PROFEDET', 1),
    (CONVERT(date,'2026-11-16'), 1, N'Conmemoración Revolución (tercer lunes de noviembre)', N'Art. 74 LFT / PROFEDET', 1),
    (CONVERT(date,'2026-12-25'), 1, N'Navidad', N'Art. 74 LFT / PROFEDET', 1)
) AS src(fecha, es_oficial, descripcion, fuente, activo)
ON tgt.fecha = src.fecha
WHEN MATCHED THEN
    UPDATE SET
        tgt.es_oficial = src.es_oficial,
        tgt.descripcion = src.descripcion,
        tgt.fuente = src.fuente,
        tgt.activo = src.activo,
        tgt.updated_at = sysdatetime(),
        tgt.updated_by = N'TI'
WHEN NOT MATCHED THEN
    INSERT (fecha, es_oficial, descripcion, fuente, activo, created_at, created_by)
    VALUES (src.fecha, src.es_oficial, src.descripcion, src.fuente, src.activo, sysdatetime(), N'TI');
GO
