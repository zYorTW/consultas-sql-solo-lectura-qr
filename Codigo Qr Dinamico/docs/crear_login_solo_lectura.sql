-- ==========================================
-- Login de solo lectura para main_dynamic.py
-- Ejecutar manualmente el DBA en SQL Server Management Studio.
-- Este script NO se ejecuta automáticamente desde la app.
-- ==========================================

USE [master];
GO

CREATE LOGIN [User_QR_SoloLectura] WITH PASSWORD = N'CAMBIA_ESTA_PASSWORD_AQUI';
GO

USE [NombreBaseDeDatos];  -- reemplaza por el valor real de DB_DATABASE en tu .env
GO

CREATE USER [User_QR_SoloLectura] FOR LOGIN [User_QR_SoloLectura];
GO

-- Solo permite SELECT sobre toda la base de datos; sin INSERT/UPDATE/DELETE/DDL/EXEC.
ALTER ROLE [db_datareader] ADD MEMBER [User_QR_SoloLectura];
GO

-- Verificación: este login no debe poder ejecutar procedimientos ni modificar datos.
-- EXECUTE AS USER = 'User_QR_SoloLectura';
-- SELECT TOP 1 * FROM sys.tables;   -- debe funcionar
-- INSERT INTO sys.tables DEFAULT VALUES; -- debe fallar con error de permisos
-- REVERT;
