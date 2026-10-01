-- Solo para desarrollo local (docker compose). En Azure SQL la base la crea el portal/Terraform.
-- Se ejecuta con: sqlcmd -d master -v DB_DATABASE=<nombre> -i 00-create-database.sql
IF DB_ID(N'$(DB_DATABASE)') IS NULL
BEGIN
    CREATE DATABASE [$(DB_DATABASE)];
    PRINT 'Base de datos $(DB_DATABASE) creada';
END
ELSE
    PRINT 'Base de datos $(DB_DATABASE) ya existe';
GO
