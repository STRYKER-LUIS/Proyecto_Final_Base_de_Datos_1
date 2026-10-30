"""Driver de PostgreSQL para el Mini IDE.

Encapsula la conexión (psycopg2), la ejecución de scripts SQL y la
normalización de resultados/errores para que los routers de FastAPI no
tengan que conocer detalles del driver.

Características principales:
- Conexión persistente en memoria con bloqueo para uso desde el threadpool
  de FastAPI.
- Ejecución de una o varias sentencias separadas por punto y coma.
- Conversión de tipos PostgreSQL (Decimal, fechas, UUID, bytea, JSONB...)
  a valores serializables en JSON.
- Errores estructurados con código, detalle, sugerencia y posición.
"""

from __future__ import annotations

import math
import os
import re
import threading
import time
from datetime import date, datetime, time as dt_time, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

import psycopg2

# ---------------------------------------------------------------------------
# Tipos OID frecuentes -> nombre legible para la cabecera de la tabla
# ---------------------------------------------------------------------------
_OID_NAMES: dict[int, str] = {
    16: "bool",
    17: "bytea",
    18: "char",
    19: "name",
    20: "int8",
    21: "int2",
    23: "int4",
    25: "text",
    26: "oid",
    114: "json",
    142: "xml",
    700: "float4",
    701: "float8",
    790: "money",
    1042: "bpchar",
    1043: "varchar",
    1082: "date",
    1083: "time",
    1114: "timestamp",
    1184: "timestamptz",
    1186: "interval",
    1266: "timetz",
    1700: "numeric",
    2950: "uuid",
    3802: "jsonb",
}

_DOLLAR_TAG_RE = re.compile(r"\$[A-Za-z_][A-Za-z0-9_]*\$|\$\$")

# Códigos SQLSTATE que tratamos de forma especial.
_SYNTAX_CODES = {"42601", "42P02", "42622"}
_SCHEMA_CODES = {"42P01", "42703", "42883", "42P07", "42701", "42702", "42P10"}
_CONFLICT_CODES = {"23505", "23503", "23502", "23514", "23P01"}
_AUTH_CODES = {"28P01", "28000"}
_CONNECTION_CODES = {"08001", "08003", "08006", "08004", "57P01", "57P02", "57P03"}


class SqlDriverError(Exception):
    """Error controlado del driver SQL con información para la UI."""

    def __init__(
        self,
        message: str,
        *,
        error_type: str = "database",
        status_code: int = 400,
        detail: str | None = None,
        hint: str | None = None,
        code: str | None = None,
        position: str | None = None,
        statement: str | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.error_type = error_type
        self.status_code = status_code
        self.detail = detail
        self.hint = hint
        self.code = code
        self.position = position
        self.statement = statement

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "type": self.error_type,
            "message": self.message,
        }
        if self.detail:
            payload["detail"] = self.detail
        if self.hint:
            payload["hint"] = self.hint
        if self.code:
            payload["code"] = self.code
        if self.position:
            payload["position"] = self.position
        if self.statement:
            payload["statement"] = self.statement
        return payload


# ---------------------------------------------------------------------------
# Utilidades de serialización
# ---------------------------------------------------------------------------
def _json_safe(value: Any) -> Any:
    """Convierte un valor de psycopg2 en algo serializable por JSON."""
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else str(value)
    if isinstance(value, Decimal):
        if not value.is_finite():
            return str(value)
        try:
            number = float(value)
            return number if math.isfinite(number) else str(value)
        except (OverflowError, ValueError):
            return str(value)
    if isinstance(value, (datetime, date, dt_time)):
        return value.isoformat()
    if isinstance(value, timedelta):
        return str(value)
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, (bytes, bytearray, memoryview)):
        return "\\x" + bytes(value).hex()
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    # Rangos, tipos geométricos, etc. Se muestran como texto.
    return str(value)


def _is_meaningful(statement: str) -> bool:
    """True si la sentencia contiene algo más que espacios y comentarios."""
    without_blocks = re.sub(r"/\*.*?\*/", " ", statement, flags=re.S)
    without_lines = re.sub(r"--[^\n]*", " ", without_blocks)
    return bool(without_lines.strip())


def split_sql_statements(script: str) -> list[str]:
    """Divide un script SQL por ';' respetando cadenas, identificadores,
    comentarios y bloques dollar-quoted ($$ ... $$ / $tag$ ... $tag$)."""
    statements: list[str] = []
    buffer: list[str] = []
    index = 0
    length = len(script)
    in_single = in_double = False
    block_depth = 0
    line_comment = False
    dollar_tag: str | None = None

    def flush() -> None:
        candidate = "".join(buffer).strip()
        if candidate and _is_meaningful(candidate):
            statements.append(candidate)
        buffer.clear()

    while index < length:
        if line_comment:
            char = script[index]
            buffer.append(char)
            index += 1
            if char == "\n":
                line_comment = False
            continue

        if block_depth > 0:
            if script.startswith("/*", index):
                block_depth += 1
                buffer.append("/*")
                index += 2
                continue
            if script.startswith("*/", index):
                block_depth -= 1
                buffer.append("*/")
                index += 2
                continue
            buffer.append(script[index])
            index += 1
            continue

        if dollar_tag is not None:
            if script.startswith(dollar_tag, index):
                buffer.append(dollar_tag)
                index += len(dollar_tag)
                dollar_tag = None
                continue
            buffer.append(script[index])
            index += 1
            continue

        if in_single:
            char = script[index]
            if char == "\\" and index + 1 < length:
                buffer.append(char)
                buffer.append(script[index + 1])
                index += 2
                continue
            if char == "'":
                if index + 1 < length and script[index + 1] == "'":
                    buffer.append("''")
                    index += 2
                    continue
                in_single = False
            buffer.append(char)
            index += 1
            continue

        if in_double:
            char = script[index]
            if char == '"':
                if index + 1 < length and script[index + 1] == '"':
                    buffer.append('""')
                    index += 2
                    continue
                in_double = False
            buffer.append(char)
            index += 1
            continue

        if script.startswith("--", index):
            line_comment = True
            buffer.append("--")
            index += 2
            continue
        if script.startswith("/*", index):
            block_depth = 1
            buffer.append("/*")
            index += 2
            continue

        char = script[index]
        if char == "'":
            in_single = True
            buffer.append(char)
            index += 1
            continue
        if char == '"':
            in_double = True
            buffer.append(char)
            index += 1
            continue
        if char == "$":
            match = _DOLLAR_TAG_RE.match(script, index)
            if match:
                dollar_tag = match.group(0)
                buffer.append(dollar_tag)
                index += len(dollar_tag)
                continue
            buffer.append(char)
            index += 1
            continue
        if char == ";":
            flush()
            index += 1
            continue

        buffer.append(char)
        index += 1

    flush()
    return statements


def _column_name(type_code: int | None) -> str:
    if type_code is None:
        return "desconocido"
    return _OID_NAMES.get(type_code, f"oid_{type_code}")


def _build_message(status: str, affected_rows: int) -> str:
    """Traduce el status de PostgreSQL a un mensaje amigable en español."""
    parts = (status or "").split()
    verb = parts[0].upper() if parts else ""
    obj = parts[1].upper() if len(parts) > 1 else ""
    objects = {
        "TABLE": ("Tabla", "creada", "eliminada", "modificada"),
        "VIEW": ("Vista", "creada", "eliminada", "modificada"),
        "INDEX": ("Índice", "creado", "eliminado", "modificado"),
        "SCHEMA": ("Esquema", "creado", "eliminado", "modificado"),
        "DATABASE": ("Base de datos", "creada", "eliminada", "modificada"),
        "SEQUENCE": ("Secuencia", "creada", "eliminada", "modificada"),
        "FUNCTION": ("Función", "creada", "eliminada", "modificada"),
        "TRIGGER": ("Disparador", "creado", "eliminado", "modificado"),
        "TYPE": ("Tipo", "creado", "eliminado", "modificado"),
        "ROLE": ("Rol", "creado", "eliminado", "modificado"),
    }
    if verb == "CREATE" and obj in objects:
        return f"{objects[obj][0]} {objects[obj][1]} correctamente."
    if verb == "DROP" and obj in objects:
        return f"{objects[obj][0]} {objects[obj][2]} correctamente."
    if verb == "ALTER" and obj in objects:
        return f"{objects[obj][0]} {objects[obj][3]} correctamente."
    if verb in {"INSERT", "UPDATE", "DELETE", "MERGE", "COPY"}:
        return f"{affected_rows} fila(s) afectada(s) por {verb}."
    if verb == "TRUNCATE":
        return "Tabla vaciada correctamente."
    if verb == "GRANT":
        return "Permisos otorgados correctamente."
    if verb == "REVOKE":
        return "Permisos revocados correctamente."
    if not verb:
        return "Comando ejecutado correctamente."
    return f"Comando ejecutado correctamente ({status})."


class PostgresDriver:
    """Gestor de la conexión PostgreSQL y ejecutor de consultas."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._conn: psycopg2.extensions.connection | None = None
        self._config: dict[str, Any] | None = None
        self._details: dict[str, Any] = {}
        self._max_rows = max(1, int(os.getenv("SQL_RESULT_LIMIT", "1000")))

    # ------------------------------------------------------------------
    # Conexión
    # ------------------------------------------------------------------
    def connect(
        self,
        *,
        host: str,
        port: int,
        user: str,
        password: str,
        dbname: str,
    ) -> dict[str, Any]:
        host = (host or "").strip()
        user = (user or "").strip()
        dbname = (dbname or "").strip()
        if not host or not user or not dbname:
            raise SqlDriverError(
                "Host, usuario y base de datos son obligatorios.",
                error_type="validation",
                status_code=422,
            )
        if not (1 <= int(port) <= 65535):
            raise SqlDriverError(
                "El puerto debe estar entre 1 y 65535.",
                error_type="validation",
                status_code=422,
            )

        config = {
            "host": host,
            "port": int(port),
            "user": user,
            "password": password,
            "dbname": dbname,
        }

        with self._lock:
            new_connection = None
            try:
                new_connection = psycopg2.connect(
                    host=host,
                    port=int(port),
                    user=user,
                    password=password,
                    dbname=dbname,
                    connect_timeout=5,
                    application_name="Mini IDE BD1",
                    client_encoding="UTF8",
                )
                new_connection.autocommit = True
                with new_connection.cursor() as cursor:
                    cursor.execute(
                        "SELECT current_database(), current_user, "
                        "inet_server_addr()::text, inet_server_port(), version()"
                    )
                    row = cursor.fetchone()

                database, current_user, server_addr, server_port, raw_version = row
                short_version = (raw_version or "PostgreSQL").split(" on ")[0]
                details = {
                    "connected": True,
                    "host": host,
                    "port": int(port),
                    "user": current_user or user,
                    "database": database or dbname,
                    "server_address": server_addr,
                    "server_version": short_version,
                    "connected_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                }
            except psycopg2.Error as exc:
                if new_connection is not None:
                    try:
                        new_connection.close()
                    except Exception:  # pragma: no cover - limpieza defensiva
                        pass
                raise self._connection_error(exc) from exc
            except Exception as exc:
                # Algunos errores del sistema/cliente (por ejemplo problemas de
                # decodificación al leer el mensaje del servidor) no heredan de
                # psycopg2.Error. Se reportan igualmente de forma estructurada.
                if new_connection is not None:
                    try:
                        new_connection.close()
                    except Exception:  # pragma: no cover
                        pass
                detail = f"{type(exc).__name__}: {exc}"
                lowered = detail.lower()
                if isinstance(exc, UnicodeDecodeError):
                    try:
                        decoded = bytes(exc.object).decode("cp1252", errors="replace")
                        detail = decoded.strip() or detail
                        lowered = detail.lower()
                    except Exception:  # pragma: no cover
                        pass
                if any(
                    marker in lowered
                    for marker in (
                        "password authentication failed",
                        "autentificación",
                        "autentificacion",
                        "contraseña",
                    )
                ):
                    raise SqlDriverError(
                        "Autenticación fallida en PostgreSQL. Revisa el usuario y la contraseña.",
                        error_type="auth",
                        status_code=401,
                        detail=detail,
                        hint="Verifica las credenciales y que el usuario tenga acceso a la base de datos.",
                    ) from exc
                raise SqlDriverError(
                    "No se pudo establecer la conexión con PostgreSQL.",
                    error_type="connection",
                    status_code=503,
                    detail=detail,
                    hint="Verifica las credenciales y la configuración regional del servidor.",
                ) from exc

            # La nueva conexión funciona: reemplazamos la anterior.
            if self._conn is not None:
                try:
                    self._conn.close()
                except Exception:  # pragma: no cover
                    pass
            self._conn = new_connection
            self._config = config
            self._details = details
            return dict(details)

    def disconnect(self) -> dict[str, Any]:
        with self._lock:
            if self._conn is not None:
                try:
                    self._conn.close()
                except Exception:  # pragma: no cover
                    pass
            self._conn = None
            self._config = None
            self._details = {}
        return {"connected": False}

    def status(self) -> dict[str, Any]:
        with self._lock:
            alive = self._conn is not None and getattr(self._conn, "closed", 1) == 0
            if not alive:
                return {"connected": False}
            return dict(self._details)

    def _reopen(self) -> None:
        """Intenta reconectar si la conexión se cerró inesperadamente."""
        if self._config is None:
            raise SqlDriverError(
                "No hay una conexión activa a PostgreSQL. Conéctate primero.",
                error_type="connection",
                status_code=409,
            )
        config = self._config
        try:
            connection = psycopg2.connect(
                **config,
                connect_timeout=5,
                application_name="Mini IDE BD1",
            )
            connection.autocommit = True
        except psycopg2.Error as exc:
            raise self._connection_error(exc) from exc
        self._conn = connection

    def _ensure_connection(self) -> None:
        if self._conn is None:
            raise SqlDriverError(
                "No hay una conexión activa a PostgreSQL. Conéctate primero.",
                error_type="connection",
                status_code=409,
            )
        if getattr(self._conn, "closed", 1) != 0:
            self._reopen()

    def _connection_error(self, exc: psycopg2.Error) -> SqlDriverError:
        text = str(exc).strip()
        pgcode = getattr(exc, "pgcode", None)
        lowered = text.lower()
        if pgcode in _AUTH_CODES or "password authentication failed" in lowered or "authentication failed" in lowered:
            return SqlDriverError(
                "Autenticación fallida en PostgreSQL. Revisa el usuario y la contraseña.",
                error_type="auth",
                status_code=401,
                detail=text,
                hint="Verifica las credenciales y que el usuario tenga acceso a la base de datos.",
            )
        if ("does not exist" in lowered and ("database" in lowered or "base de datos" in lowered)) or (
            "base de datos" in lowered and "no existe" in lowered
        ):
            return SqlDriverError(
                "La base de datos indicada no existe en el servidor.",
                error_type="config",
                status_code=400,
                detail=text,
                hint="Crea la base de datos o corrige el campo 'Base de datos'.",
            )
        if "could not connect" in lowered or "connection refused" in lowered or "timeout" in lowered:
            return SqlDriverError(
                "No se pudo conectar a PostgreSQL. Verifica host, puerto y que el servidor esté en ejecución.",
                error_type="connection",
                status_code=503,
                detail=text,
                hint="Comprueba que el servicio de PostgreSQL esté iniciado y escuchando en el host/puerto indicados.",
            )
        return SqlDriverError(
            "No se pudo establecer la conexión con PostgreSQL.",
            error_type="connection",
            status_code=503,
            detail=text,
        )

    # ------------------------------------------------------------------
    # Ejecución
    # ------------------------------------------------------------------
    def execute(self, script: str) -> dict[str, Any]:
        if not script or not script.strip():
            raise SqlDriverError(
                "La consulta está vacía. Escribe al menos una sentencia SQL.",
                error_type="validation",
                status_code=422,
            )
        statements = split_sql_statements(script)
        if not statements:
            raise SqlDriverError(
                "La consulta solo contiene comentarios o espacios en blanco.",
                error_type="validation",
                status_code=422,
            )

        started = time.perf_counter()
        with self._lock:
            self._ensure_connection()
            results: list[dict[str, Any]] = []
            for position, statement in enumerate(statements):
                try:
                    results.append(self._execute_statement(statement))
                except psycopg2.Error as exc:
                    self._safe_rollback()
                    raise self._translate_execution_error(
                        exc, statement=statement, position=position
                    ) from exc
                except Exception as exc:
                    self._safe_rollback()
                    raise SqlDriverError(
                        f"Error inesperado al ejecutar la sentencia {position + 1}.",
                        error_type="database",
                        status_code=500,
                        detail=f"{type(exc).__name__}: {exc}",
                        statement=statement,
                    ) from exc

        return {
            "results": results,
            "duration_ms": round((time.perf_counter() - started) * 1000, 2),
            "statement_count": len(statements),
        }

    def _execute_statement(self, statement: str) -> dict[str, Any]:
        assert self._conn is not None
        with self._conn.cursor() as cursor:
            cursor.execute(statement)
            if cursor.description is not None:
                columns = [
                    {"name": desc[0], "type": _column_name(desc[1])}
                    for desc in cursor.description
                ]
                fetched = cursor.fetchmany(self._max_rows + 1)
                truncated = len(fetched) > self._max_rows
                rows = [_json_safe(item) for item in fetched[: self._max_rows]]
                return {
                    "type": "query",
                    "kind": "SELECT",
                    "statement": statement.strip(),
                    "columns": columns,
                    "rows": rows,
                    "row_count": len(rows),
                    "truncated": truncated,
                    "max_rows": self._max_rows,
                }

            affected = cursor.rowcount if cursor.rowcount is not None and cursor.rowcount >= 0 else 0
            status = cursor.statusmessage or ""
            return {
                "type": "command",
                "kind": status.split()[0] if status else "DDL/DML",
                "statement": statement.strip(),
                "command": status,
                "affected_rows": affected,
                "message": _build_message(status, affected),
            }

    def _safe_rollback(self) -> None:
        if self._conn is None:
            return
        try:
            self._conn.rollback()
        except Exception:  # pragma: no cover - mejor esfuerzo
            pass

    def _translate_execution_error(
        self, exc: psycopg2.Error, *, statement: str, position: int
    ) -> SqlDriverError:
        diag = getattr(exc, "diag", None)
        pgcode = getattr(exc, "pgcode", None)
        primary = (getattr(diag, "message_primary", None) or str(exc)).strip()
        detail = getattr(diag, "message_detail", None)
        hint = getattr(diag, "hint", None)
        char_position = getattr(diag, "statement_position", None)
        prefix = f"Sentencia {position + 1}: " if position else ""

        if isinstance(exc, psycopg2.OperationalError) and not pgcode:
            return SqlDriverError(
                "Se perdió la conexión con PostgreSQL. Vuelve a conectar e intenta de nuevo.",
                error_type="connection",
                status_code=503,
                detail=primary,
                hint=hint,
                code=pgcode,
                statement=statement,
            )
        if pgcode in _AUTH_CODES:
            return SqlDriverError(
                f"{prefix}Autenticación/permiso denegado por PostgreSQL.",
                error_type="permission",
                status_code=403,
                detail=primary,
                hint=hint,
                code=pgcode,
                position=char_position,
                statement=statement,
            )
        if pgcode in _SYNTAX_CODES:
            return SqlDriverError(
                f"{prefix}Error de sintaxis SQL.",
                error_type="syntax",
                status_code=400,
                detail=primary,
                hint=hint or "Revisa comillas, paréntesis, comas y palabras clave cercanas a la posición indicada.",
                code=pgcode,
                position=char_position,
                statement=statement,
            )
        if pgcode in _SCHEMA_CODES:
            return SqlDriverError(
                f"{prefix}Objeto o columna no encontrado.",
                error_type="schema",
                status_code=400,
                detail=primary,
                hint=hint or "Verifica el nombre de tablas, columnas y que el esquema/search_path sea el correcto.",
                code=pgcode,
                position=char_position,
                statement=statement,
            )
        if pgcode in _CONFLICT_CODES:
            return SqlDriverError(
                f"{prefix}Conflicto de integridad de datos.",
                error_type="conflict",
                status_code=409,
                detail=primary,
                hint=hint or "Revisa las restricciones UNIQUE, FOREIGN KEY, NOT NULL o CHECK de la tabla.",
                code=pgcode,
                position=char_position,
                statement=statement,
            )
        if pgcode == "42501":
            return SqlDriverError(
                f"{prefix}Permisos insuficientes para ejecutar la sentencia.",
                error_type="permission",
                status_code=403,
                detail=primary,
                hint=hint,
                code=pgcode,
                position=char_position,
                statement=statement,
            )
        if pgcode in {"40001", "40P01"}:
            return SqlDriverError(
                f"{prefix}Conflicto de concurrencia. Intenta ejecutar de nuevo.",
                error_type="conflict",
                status_code=409,
                detail=primary,
                hint=hint,
                code=pgcode,
                statement=statement,
            )
        if pgcode == "57014":
            return SqlDriverError(
                f"{prefix}La consulta fue cancelada o superó el tiempo de espera.",
                error_type="timeout",
                status_code=408,
                detail=primary,
                hint=hint or "Simplifica la consulta o agrega filtros/índices.",
                code=pgcode,
                statement=statement,
            )
        if pgcode in _CONNECTION_CODES:
            return SqlDriverError(
                f"{prefix}Problema de conexión con PostgreSQL.",
                error_type="connection",
                status_code=503,
                detail=primary,
                hint=hint,
                code=pgcode,
                statement=statement,
            )
        return SqlDriverError(
            f"{prefix}Error al ejecutar la sentencia SQL.",
            error_type="database",
            status_code=400 if pgcode else 500,
            detail=primary,
            hint=hint,
            code=pgcode,
            position=char_position,
            statement=statement,
        )


# Instancia única compartida por los routers.
postgres_driver = PostgresDriver()
