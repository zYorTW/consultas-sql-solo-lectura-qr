"""Batería de seguridad para validate_readonly_sql: python test_security.py

Verifica que TODA sentencia que no sea un SELECT único de solo lectura sea
rechazada, incluyendo intentos de evasión. No usa frameworks: solo asserts.
"""
from main_dynamic import validate_readonly_sql, SQLSecurityError


def is_blocked(sql):
    try:
        validate_readonly_sql(sql)
        return False
    except SQLSecurityError:
        return True


# ---------------------------------------------------------------------------
# 1. Palabras clave que el requerimiento exige bloquear (explícitas)
# ---------------------------------------------------------------------------
BLOCKED = [
    "INSERT INTO T VALUES (1)",
    "UPDATE T SET X = 1",
    "DELETE FROM T",
    "DROP TABLE T",
    "ALTER TABLE T ADD C INT",
    "CREATE TABLE T (C INT)",
    "TRUNCATE TABLE T",
    "MERGE INTO T USING S ON T.ID = S.ID WHEN MATCHED THEN DELETE",
    "EXEC algo",
    "EXECUTE algo",
    "USE otra_base",
    "GRANT SELECT ON T TO usuario",
    "REVOKE SELECT ON T FROM usuario",
    "DENY SELECT ON T TO usuario",
    "BACKUP DATABASE db TO DISK = 'x.bak'",
    "RESTORE DATABASE db FROM DISK = 'x.bak'",
    # SELECT INTO (crea tabla) — cubierto por INTO
    "SELECT * INTO nueva_tabla FROM T",
    "SELECT * INTO #temporal FROM T",
    "SELECT * INTO ##global FROM T",
    # procedimientos almacenados
    "EXEC sp_who",
    "EXEC xp_cmdshell 'dir'",
    "SELECT * FROM sp_helptext",
    "SELECT * FROM xp_fileexist",
    # administración / DoS / exfiltración
    "SELECT 1 SHUTDOWN",
    "SELECT 1 KILL 55",
    "SELECT 1 DBCC FREEPROCCACHE",
    "SELECT 1 RECONFIGURE",
    "SELECT 1 WAITFOR DELAY '00:00:10'",
    "BULK INSERT T FROM 'x.txt'",
    "SELECT * FROM OPENROWSET('SQLNCLI', '...', 'SELECT 1')",
    "SELECT * FROM OPENQUERY(srv, 'SELECT 1')",
    "SELECT * FROM OPENDATASOURCE('x', 'y')",
]

# ---------------------------------------------------------------------------
# 2. Estructura: múltiples sentencias, vacías, no-SELECT
# ---------------------------------------------------------------------------
STRUCTURE = [
    "",
    "   ",
    ";",
    "SELECT 1; DROP TABLE T",           # apiladas con ;
    "SELECT 1; SELECT 2",               # dos SELECT apilados
    "DROP TABLE T; SELECT 1",           # dañina primero
    "WITH c AS (SELECT 1) DELETE FROM T",   # CTE que no es SELECT puro
    "UPDATE T SET X=1 WHERE ID=(SELECT MAX(ID) FROM T)",  # DML con SELECT dentro
]

# ---------------------------------------------------------------------------
# 3. Evasión: mayúsculas/minúsculas, comentarios, espacios, saltos de línea
# ---------------------------------------------------------------------------
EVASION = [
    "delete from t",                    # minúsculas
    "DeLeTe FROM T",                    # mixto
    "  DELETE FROM T  ",                # espacios
    "DELETE\nFROM\nT",                  # saltos de línea
    "/* comentario */ DELETE FROM T",   # comentario antes
    "DELETE FROM T -- comentario",      # comentario de línea
    "SELECT * FROM T; -- inofensivo\nDROP TABLE T",  # 2da sentencia tras comentario
    "SELECT 1 UPDATE T SET X=1",        # DML sin ';' de separador
    "SELECT 1 DROP TABLE T",
]

# ---------------------------------------------------------------------------
# 4. SELECT legítimos que DEBEN pasar (sin falsos positivos)
# ---------------------------------------------------------------------------
ALLOWED = [
    "SELECT * FROM T WHERE X = ?",
    "SELECT TOP 100 A, B FROM T WHERE X = ?",
    "select nombre from empleados where id = ?",
    "SELECT 'delete' AS palabra FROM T WHERE X = 1",   # keyword solo en literal
    "SELECT 'DROP TABLE T' AS texto WHERE 1 = 1",      # keyword solo en literal
    "SELECT A FROM T WHERE nombre = 'insert; update'", # ; y keywords en literal
    "SELECT A /* update aquí no cuenta */ FROM T WHERE X = 1",  # keyword en comentario
    "SELECT COUNT(*) AS total FROM T",
    "SELECT A FROM T WHERE nota LIKE '%into%'",        # 'into' dentro de literal
]


def run():
    failures = []

    for sql in BLOCKED + STRUCTURE + EVASION:
        if not is_blocked(sql):
            failures.append(f"NO BLOQUEÓ (debía): {sql!r}")

    for sql in ALLOWED:
        if is_blocked(sql):
            failures.append(f"BLOQUEÓ de más (falso positivo): {sql!r}")

    total = len(BLOCKED) + len(STRUCTURE) + len(EVASION) + len(ALLOWED)
    if failures:
        print(f"FALLARON {len(failures)} de {total} casos:\n")
        for f in failures:
            print("  -", f)
        raise SystemExit(1)

    print(f"OK: {total} casos de seguridad pasaron")
    print(f"  {len(BLOCKED)} palabras clave bloqueadas")
    print(f"  {len(STRUCTURE)} casos de estructura")
    print(f"  {len(EVASION)} intentos de evasión")
    print(f"  {len(ALLOWED)} SELECT legítimos sin falsos positivos")


if __name__ == "__main__":
    run()
