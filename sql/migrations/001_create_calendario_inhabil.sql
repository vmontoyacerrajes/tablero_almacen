IF OBJECT_ID('dbo.calendario_inhabil','U') IS NULL
BEGIN
    CREATE TABLE dbo.calendario_inhabil (
        fecha           date            NOT NULL,
        es_oficial      bit             NOT NULL CONSTRAINT DF_cal_inh_es_oficial DEFAULT (1),
        descripcion     nvarchar(200)   NULL,
        fuente          nvarchar(200)   NULL,
        activo          bit             NOT NULL CONSTRAINT DF_cal_inh_activo DEFAULT (1),
        created_at      datetime2(0)    NOT NULL CONSTRAINT DF_cal_inh_created DEFAULT (sysdatetime()),
        created_by      nvarchar(100)   NULL,
        updated_at      datetime2(0)    NULL,
        updated_by      nvarchar(100)   NULL,
        CONSTRAINT PK_calendario_inhabil PRIMARY KEY (fecha)
    );

    CREATE INDEX IX_cal_inh_activo_fecha
    ON dbo.calendario_inhabil(activo, fecha);
END;
GO
