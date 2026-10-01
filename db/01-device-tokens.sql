-- Esquema de la tabla device_tokens. Idempotente y portable: sirve igual en local y en Azure SQL.
-- No usa USE: se ejecuta contra la base ya seleccionada
--   sqlcmd -d <base> -i 01-device-tokens.sql
-- Es la ÚNICA definición del esquema; la app solo verifica que exista al arrancar.
IF OBJECT_ID(N'dbo.device_tokens', N'U') IS NULL
BEGIN
    CREATE TABLE dbo.device_tokens (
        user_id    NVARCHAR(128) NOT NULL CONSTRAINT PK_device_tokens PRIMARY KEY,
        fid        NVARCHAR(256) NOT NULL,
        updated_at DATETIME2     NOT NULL
    );
    PRINT 'Tabla dbo.device_tokens creada';
END
ELSE
    PRINT 'Tabla dbo.device_tokens ya existe';
GO
