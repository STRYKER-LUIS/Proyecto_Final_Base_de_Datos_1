"""Driver de MongoDB para el Mini IDE.

Interpreta una sintaxis reducida del shell de MongoDB
(``db.coleccion.find({...}).sort({...}).limit(5)``) y también comandos
descritos en JSON. Devuelve documentos normalizados y errores estructurados.

Operaciones soportadas:
- createCollection, drop, dropDatabase, listCollections
- insertOne, insertMany
- find, findOne, aggregate, countDocuments, distinct
- updateOne, updateMany, replaceOne
- deleteOne, deleteMany
- createIndex
"""

from __future__ import annotations

import base64
import math
import os
import re
import threading
import time
from datetime import date, datetime, timezone
from decimal import Decimal
from itertools import islice
from typing import Any

from bson import ObjectId
from bson.binary import Binary
from bson.decimal128 import Decimal128
from bson.max_key import MaxKey
from bson.min_key import MinKey
from bson.regex import Regex
from bson.timestamp import Timestamp
from pymongo import MongoClient
from pymongo.database import Database
from pymongo.errors import (
    AutoReconnect,
    ConfigurationError,
    ConnectionFailure,
    DuplicateKeyError,
    InvalidURI,
    OperationFailure,
    PyMongoError,
    ServerSelectionTimeoutError,
    WriteError,
)
from pymongo.uri_parser import parse_uri


class MongoDriverError(Exception):
    """Error controlado del driver NoSQL con información para la UI."""

    def __init__(
        self,
        message: str,
        *,
        error_type: str = "database",
        status_code: int = 400,
        detail: str | None = None,
        hint: str | None = None,
        code: str | int | None = None,
        statement: str | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.error_type = error_type
        self.status_code = status_code
        self.detail = detail
        self.hint = hint
        self.code = code
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
        if self.code is not None:
            payload["code"] = self.code
        if self.statement:
            payload["statement"] = self.statement
        return payload


class MongoSyntaxError(ValueError):
    """Error interno del parser de la sintaxis shell/JSON."""


# ---------------------------------------------------------------------------
# Serialización de tipos BSON
# ---------------------------------------------------------------------------
def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else str(value)
    if isinstance(value, ObjectId):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal128):
        number = value.to_decimal()
        return _json_safe(number)
    if isinstance(value, Decimal):
        if not value.is_finite():
            return str(value)
        try:
            number = float(value)
            return number if math.isfinite(number) else str(value)
        except (OverflowError, ValueError):
            return str(value)
    if isinstance(value, Timestamp):
        return {"$timestamp": {"t": value.time, "i": value.inc}}
    if isinstance(value, Binary):
        return base64.b64encode(bytes(value)).decode("ascii")
    if isinstance(value, Regex):
        return f"/{value.pattern}/{value.flags}" if value.flags else f"/{value.pattern}/"
    if isinstance(value, MinKey):
        return "MinKey"
    if isinstance(value, MaxKey):
        return "MaxKey"
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    return str(value)


def _document_columns(documents: list[dict[str, Any]]) -> list[str]:
    seen: list[str] = []
    known: set[str] = set()
    for document in documents:
        if not isinstance(document, dict):
            continue
        for key in document:
            if key not in known:
                known.add(key)
                seen.append(key)
    return seen


def _strip_js_comments(text: str) -> str:
    """Quita comentarios // y /* */ respetando cadenas, para inspecciones."""
    output: list[str] = []
    index = 0
    length = len(text)
    quote: str | None = None
    while index < length:
        char = text[index]
        if quote is not None:
            output.append(char)
            if char == "\\" and index + 1 < length:
                output.append(text[index + 1])
                index += 2
                continue
            if char == quote:
                quote = None
            index += 1
            continue
        if text.startswith("//", index):
            newline = text.find("\n", index + 2)
            if newline < 0:
                break
            output.append("\n")
            index = newline + 1
            continue
        if text.startswith("/*", index):
            end = text.find("*/", index + 2)
            if end < 0:
                break
            output.append(" ")
            index = end + 2
            continue
        if char in {"'", '"'}:
            quote = char
        output.append(char)
        index += 1
    return "".join(output)


def _next_starts_command(rest: str) -> bool:
    """Indica si el siguiente fragmento de código empieza un comando nuevo."""
    index = 0
    length = len(rest)
    while index < length:
        if rest[index].isspace():
            index += 1
            continue
        if rest.startswith("//", index):
            newline = rest.find("\n", index + 2)
            if newline < 0:
                return False
            index = newline + 1
            continue
        if rest.startswith("/*", index):
            end = rest.find("*/", index + 2)
            if end < 0:
                return False
            index = end + 2
            continue
        return rest.startswith("db.", index) or rest.startswith("db ", index) or rest[index] == "{"
    return False


def split_mongo_statements(script: str) -> list[str]:
    """Divide comandos por ';' o por salto de línea de nivel superior.

    Permite pegar varios comandos del shell sin punto y coma, uno por línea.
    """
    statements: list[str] = []
    buffer: list[str] = []
    index = 0
    length = len(script)
    depth = 0
    quote: str | None = None
    line_comment = False
    block_comment = False

    def flush() -> None:
        candidate = "".join(buffer).strip()
        if candidate and _strip_js_comments(candidate).strip(" ;\n\t"):
            statements.append(candidate)
        buffer.clear()

    while index < length:
        char = script[index]

        if line_comment:
            buffer.append(char)
            index += 1
            if char == "\n":
                line_comment = False
            continue

        if block_comment:
            if script.startswith("*/", index):
                buffer.append("*/")
                index += 2
                block_comment = False
                continue
            buffer.append(char)
            index += 1
            continue

        if quote is not None:
            buffer.append(char)
            if char == "\\" and index + 1 < length:
                buffer.append(script[index + 1])
                index += 2
                continue
            if char == quote:
                quote = None
            index += 1
            continue

        if script.startswith("//", index):
            line_comment = True
            buffer.append("//")
            index += 2
            continue
        if script.startswith("/*", index):
            block_comment = True
            buffer.append("/*")
            index += 2
            continue

        if char in {"'", '"'}:
            quote = char
            buffer.append(char)
            index += 1
            continue
        if char in "{([":
            depth += 1
            buffer.append(char)
            index += 1
            continue
        if char in "})]":
            depth = max(0, depth - 1)
            buffer.append(char)
            index += 1
            continue
        if depth == 0 and char == ";":
            flush()
            index += 1
            continue
        if depth == 0 and char == "\n":
            buffer.append(char)
            cleaned = _strip_js_comments("".join(buffer)).strip()
            if cleaned.endswith((")", "}", "]")) and _next_starts_command(script[index + 1 :]):
                flush()
            index += 1
            continue

        buffer.append(char)
        index += 1

    flush()
    return statements


# ---------------------------------------------------------------------------
# Parser de valores tipo shell de MongoDB
# ---------------------------------------------------------------------------
_NUMBER_RE = re.compile(r"[-+]?(?:\d+\.\d*|\.\d+|\d+)(?:[eE][-+]?\d+)?")
_IDENTIFIER_RE = re.compile(r"[A-Za-z_$][A-Za-z0-9_$]*")
_A_KEY_RE = re.compile(r"[A-Za-z_$][A-Za-z0-9_$.]*")


class _Parser:
    def __init__(self, text: str) -> None:
        self.text = text
        self.pos = 0
        self.length = len(text)

    # -- utilidades ----------------------------------------------------
    def error(self, message: str) -> None:
        line = self.text.count("\n", 0, self.pos) + 1
        last_newline = self.text.rfind("\n", 0, self.pos)
        column = self.pos - last_newline
        raise MongoSyntaxError(f"{message} (línea {line}, columna {column})")

    def skip(self) -> None:
        while self.pos < self.length:
            char = self.text[self.pos]
            if char.isspace():
                self.pos += 1
                continue
            if self.text.startswith("//", self.pos):
                newline = self.text.find("\n", self.pos + 2)
                self.pos = self.length if newline < 0 else newline + 1
                continue
            if self.text.startswith("/*", self.pos):
                end = self.text.find("*/", self.pos + 2)
                if end < 0:
                    self.error("Comentario de bloque sin cerrar")
                self.pos = end + 2
                continue
            break

    def peek(self) -> str | None:
        self.skip()
        return self.text[self.pos] if self.pos < self.length else None

    def expect(self, char: str) -> None:
        self.skip()
        if self.pos >= self.length or self.text[self.pos] != char:
            self.error(f"Se esperaba '{char}'")
        self.pos += 1

    def identifier(self) -> str:
        self.skip()
        match = _IDENTIFIER_RE.match(self.text, self.pos)
        if not match:
            self.error("Se esperaba un identificador")
        self.pos = match.end()
        return match.group(0)

    # -- valores -------------------------------------------------------
    def value(self) -> Any:
        self.skip()
        if self.pos >= self.length:
            self.error("Se esperaba un valor")
        char = self.text[self.pos]

        if char == "{":
            return self.object()
        if char == "[":
            return self.array()
        if char in {"'", '"'}:
            return self.string()
        if char == "/":
            return self.regex()
        if char.isdigit() or (char in "+-." and self.pos + 1 < self.length and
                              (self.text[self.pos + 1].isdigit() or self.text[self.pos + 1] == ".")):
            return self.number()
        if char.isalpha() or char in "_$":
            word = self.identifier()
            lowered = word.lower()
            if lowered == "true":
                return True
            if lowered == "false":
                return False
            if lowered in {"null", "undefined"}:
                return None
            if lowered == "new":
                self.skip()
                constructor = self.identifier()
                return self.constructor(constructor)
            self.skip()
            if self.pos < self.length and self.text[self.pos] == "(":
                return self.constructor(word)
            self.error(
                f"Valor no reconocido '{word}'. Las cadenas deben ir entre comillas."
            )
        self.error(f"Carácter inesperado '{char}'")

    def string(self) -> str:
        self.skip()
        quote = self.text[self.pos]
        self.pos += 1
        chunks: list[str] = []
        escapes = {
            "n": "\n", "t": "\t", "r": "\r", "b": "\b", "f": "\f",
            '"': '"', "'": "'", "\\": "\\", "/": "/",
        }
        while self.pos < self.length:
            char = self.text[self.pos]
            if char == "\\":
                self.pos += 1
                if self.pos >= self.length:
                    break
                escaped = self.text[self.pos]
                if escaped == "u" and self.pos + 4 < self.length:
                    hex_value = self.text[self.pos + 1 : self.pos + 5]
                    try:
                        chunks.append(chr(int(hex_value, 16)))
                    except ValueError:
                        self.error("Secuencia unicode inválida")
                    self.pos += 5
                    continue
                chunks.append(escapes.get(escaped, escaped))
                self.pos += 1
                continue
            if char == quote:
                self.pos += 1
                return "".join(chunks)
            chunks.append(char)
            self.pos += 1
        self.error("Cadena sin cerrar")

    def number(self) -> int | float:
        match = _NUMBER_RE.match(self.text, self.pos)
        if not match:
            self.error("Número inválido")
        token = match.group(0)
        self.pos = match.end()
        if any(marker in token.lower() for marker in (".", "e")):
            return float(token)
        return int(token)

    def regex(self) -> Regex:
        self.pos += 1  # '/'
        pattern: list[str] = []
        in_class = False
        while self.pos < self.length:
            char = self.text[self.pos]
            if char == "\\" and self.pos + 1 < self.length:
                pattern.append(char)
                pattern.append(self.text[self.pos + 1])
                self.pos += 2
                continue
            if char == "[":
                in_class = True
            elif char == "]":
                in_class = False
            elif char == "/" and not in_class:
                self.pos += 1
                break
            pattern.append(char)
            self.pos += 1
        else:
            self.error("Expresión regular sin cerrar")
        flags_start = self.pos
        while self.pos < self.length and self.text[self.pos].isalpha():
            self.pos += 1
        return Regex("".join(pattern), self.text[flags_start : self.pos])

    def object(self) -> dict[str, Any]:
        self.expect("{")
        result: dict[str, Any] = {}
        self.skip()
        if self.peek() == "}":
            self.pos += 1
            return result
        while True:
            key = self.key()
            self.skip()
            self.expect(":")
            result[key] = self.value()
            self.skip()
            if self.pos >= self.length:
                self.error("Objeto sin cerrar")
            char = self.text[self.pos]
            if char == ",":
                self.pos += 1
                self.skip()
                if self.peek() == "}":
                    self.pos += 1
                    return result
                continue
            if char == "}":
                self.pos += 1
                return result
            self.error("Se esperaba ',' o '}'")

    def array(self) -> list[Any]:
        self.expect("[")
        result: list[Any] = []
        self.skip()
        if self.peek() == "]":
            self.pos += 1
            return result
        while True:
            result.append(self.value())
            self.skip()
            if self.pos >= self.length:
                self.error("Arreglo sin cerrar")
            char = self.text[self.pos]
            if char == ",":
                self.pos += 1
                self.skip()
                if self.peek() == "]":
                    self.pos += 1
                    return result
                continue
            if char == "]":
                self.pos += 1
                return result
            self.error("Se esperaba ',' o ']'")

    def key(self) -> str:
        self.skip()
        if self.pos < self.length and self.text[self.pos] in {"'", '"'}:
            return self.string()
        match = _A_KEY_RE.match(self.text, self.pos)
        if not match:
            self.error("Se esperaba el nombre de un campo")
        self.pos = match.end()
        return match.group(0)

    def arguments(self) -> list[Any]:
        self.expect("(")
        args: list[Any] = []
        self.skip()
        if self.peek() == ")":
            self.pos += 1
            return args
        while True:
            args.append(self.value())
            self.skip()
            if self.pos >= self.length:
                self.error("Llamada sin cerrar")
            char = self.text[self.pos]
            if char == ",":
                self.pos += 1
                self.skip()
                if self.peek() == ")":
                    self.pos += 1
                    return args
                continue
            if char == ")":
                self.pos += 1
                return args
            self.error("Se esperaba ',' o ')'")

    def constructor(self, name: str) -> Any:
        args = self.arguments()
        lowered = name.lower()
        if lowered == "objectid":
            if not args or not isinstance(args[0], str):
                self.error("ObjectId requiere una cadena")
            try:
                return ObjectId(args[0])
            except Exception as exc:  # InvalidId
                raise MongoSyntaxError(f"ObjectId inválido: {args[0]}") from exc
        if lowered in {"isodate", "date"}:
            if not args:
                return datetime.now(timezone.utc)
            return _parse_datetime(args[0])
        if lowered in {"numberlong", "numberint", "int32", "int64"}:
            if not args:
                self.error(f"{name} requiere un valor")
            return int(args[0])
        if lowered == "numberdecimal":
            if not args:
                self.error("NumberDecimal requiere un valor")
            return Decimal128(str(args[0]))
        if lowered == "timestamp":
            if len(args) < 2:
                self.error("Timestamp requiere (t, i)")
            return Timestamp(int(args[0]), int(args[1]))
        if lowered == "binary":
            if not args:
                self.error("Binary requiere un valor")
            raw = args[-1]
            if isinstance(raw, str):
                try:
                    raw = base64.b64decode(raw)
                except Exception:
                    raw = raw.encode("utf-8")
            subtype = int(args[1]) if len(args) > 1 and isinstance(args[1], int) else 0
            return Binary(raw, subtype)
        if lowered == "minkey":
            return MinKey()
        if lowered == "maxkey":
            return MaxKey()
        self.error(f"Constructor '{name}' no soportado")

    def finish(self) -> None:
        self.skip()
        if self.pos < self.length:
            snippet = self.text[self.pos : self.pos + 30].replace("\n", " ")
            self.error(f"Texto inesperado: '{snippet}'")


def _parse_datetime(raw: Any) -> datetime:
    if isinstance(raw, datetime):
        return raw
    text = str(raw).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
            try:
                return datetime.strptime(text, fmt)
            except ValueError:
                continue
    raise MongoSyntaxError(f"Fecha inválida: {raw}")


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------
_ACTION_ALIASES = {
    "createcollection": "createCollection",
    "dropcollection": "drop",
    "drop": "drop",
    "dropdatabase": "dropDatabase",
    "listcollections": "listCollections",
    "getcollectionnames": "listCollections",
    "insertone": "insertOne",
    "insert": "insertOne",
    "insertmany": "insertMany",
    "find": "find",
    "findone": "findOne",
    "aggregate": "aggregate",
    "count": "countDocuments",
    "countdocuments": "countDocuments",
    "distinct": "distinct",
    "updateone": "updateOne",
    "update": "updateOne",
    "updatemany": "updateMany",
    "replaceone": "replaceOne",
    "deleteone": "deleteOne",
    "delete": "deleteOne",
    "deletemany": "deleteMany",
    "createindex": "createIndex",
}

_COLLECTION_METHODS = {
    "insertOne", "insertMany", "find", "findOne", "aggregate",
    "countDocuments", "distinct", "updateOne", "updateMany", "replaceOne",
    "deleteOne", "deleteMany", "createIndex", "drop",
}


class MongoDriver:
    """Gestor de la conexión MongoDB y ejecutor de comandos."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._client: MongoClient | None = None
        self._db: Database | None = None
        self._details: dict[str, Any] = {}
        self._default_limit = max(1, int(os.getenv("MONGO_RESULT_LIMIT", "100")))
        self._hard_limit = 1000

    # ------------------------------------------------------------------
    # Conexión
    # ------------------------------------------------------------------
    def connect(self, *, uri: str, database: str | None = None) -> dict[str, Any]:
        uri = (uri or "").strip()
        if not uri:
            raise MongoDriverError(
                "La URI de MongoDB es obligatoria.",
                error_type="validation",
                status_code=422,
            )
        if not uri.startswith(("mongodb://", "mongodb+srv://")):
            raise MongoDriverError(
                "La URI debe comenzar con 'mongodb://' o 'mongodb+srv://'.",
                error_type="validation",
                status_code=422,
            )

        try:
            parsed = parse_uri(uri)
        except (InvalidURI, ConfigurationError, ValueError) as exc:
            raise MongoDriverError(
                "La URI de conexión no es válida.",
                error_type="config",
                status_code=400,
                detail=str(exc),
                hint="Formato esperado: mongodb://usuario:clave@host:27017/base_de_datos",
            ) from exc

        db_name = parsed.get("database") or (database or "").strip() or "mini_ide_bd1"
        if any(char in db_name for char in ' /\\."$*<>:|?'):
            raise MongoDriverError(
                f"El nombre de base de datos '{db_name}' no es válido.",
                error_type="validation",
                status_code=422,
                hint="Evita espacios y los caracteres / \\ . \" $ * < > : | ?",
            )

        new_client: MongoClient | None = None
        try:
            new_client = MongoClient(
                uri,
                serverSelectionTimeoutMS=5000,
                connectTimeoutMS=5000,
                tz_aware=True,
            )
            new_client.admin.command("ping")
            version = "MongoDB"
            try:
                version = f"MongoDB {new_client.server_info().get('version', '')}".strip()
            except PyMongoError:
                pass
            database_obj = new_client[db_name]
            nodelist = parsed.get("nodelist") or []
            hosts = ", ".join(f"{host}:{port}" for host, port in nodelist)
            details = {
                "connected": True,
                "database": db_name,
                "hosts": hosts,
                "server_version": version,
                "connected_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            }
        except PyMongoError as exc:
            if new_client is not None:
                try:
                    new_client.close()
                except Exception:  # pragma: no cover
                    pass
            raise self._connection_error(exc) from exc

        if self._client is not None:
            try:
                self._client.close()
            except Exception:  # pragma: no cover
                pass
        self._client = new_client
        self._db = database_obj
        self._details = details
        return dict(details)

    def disconnect(self) -> dict[str, Any]:
        with self._lock:
            if self._client is not None:
                try:
                    self._client.close()
                except Exception:  # pragma: no cover
                    pass
            self._client = None
            self._db = None
            self._details = {}
        return {"connected": False}

    def status(self) -> dict[str, Any]:
        with self._lock:
            if self._client is None or self._db is None:
                return {"connected": False}
            return dict(self._details)

    def _connection_error(self, exc: PyMongoError) -> MongoDriverError:
        text = str(exc).strip()
        if isinstance(exc, OperationFailure) and exc.code == 18:
            return MongoDriverError(
                "Autenticación fallida en MongoDB. Revisa usuario, contraseña y authSource.",
                error_type="auth",
                status_code=401,
                detail=text,
                hint="Si usas autenticación, agrega ?authSource=admin o la base correcta en la URI.",
            )
        if isinstance(exc, (ServerSelectionTimeoutError, ConnectionFailure, AutoReconnect)):
            return MongoDriverError(
                "No se pudo conectar a MongoDB. Verifica la URI y que mongod esté en ejecución.",
                error_type="connection",
                status_code=503,
                detail=text,
                hint="Comprueba el host/puerto (por defecto 27017) y que el servicio no esté bloqueado por un firewall.",
            )
        if isinstance(exc, ConfigurationError):
            return MongoDriverError(
                "La configuración de la conexión a MongoDB no es válida.",
                error_type="config",
                status_code=400,
                detail=text,
            )
        return MongoDriverError(
            "No se pudo establecer la conexión con MongoDB.",
            error_type="connection",
            status_code=503,
            detail=text,
        )

    def _require_db(self) -> Database:
        if self._client is None or self._db is None:
            raise MongoDriverError(
                "No hay una conexión activa a MongoDB. Conéctate primero.",
                error_type="connection",
                status_code=409,
            )
        return self._db

    # ------------------------------------------------------------------
    # Ejecución
    # ------------------------------------------------------------------
    def execute(self, script: str) -> dict[str, Any]:
        if not script or not script.strip():
            raise MongoDriverError(
                "El comando está vacío. Escribe una operación de MongoDB.",
                error_type="validation",
                status_code=422,
            )
        statements = split_mongo_statements(script)
        if not statements:
            raise MongoDriverError(
                "El comando solo contiene comentarios o espacios en blanco.",
                error_type="validation",
                status_code=422,
            )

        started = time.perf_counter()
        results: list[dict[str, Any]] = []
        with self._lock:
            db = self._require_db()
            for position, statement in enumerate(statements):
                try:
                    results.append(self._execute_single(db, statement))
                except MongoSyntaxError as exc:
                    raise MongoDriverError(
                        f"Comando {position + 1}: sintaxis no válida.",
                        error_type="syntax",
                        status_code=400,
                        detail=str(exc),
                        hint="Usa la sintaxis del shell: db.coleccion.metodo({...}) o un comando JSON.",
                        statement=statement,
                    ) from exc
                except MongoDriverError:
                    raise
                except PyMongoError as exc:
                    raise self._translate_execution_error(
                        exc, statement=statement, position=position
                    ) from exc

        return {
            "results": results,
            "duration_ms": round((time.perf_counter() - started) * 1000, 2),
            "statement_count": len(statements),
        }

    def _execute_single(self, db: Database, statement: str) -> dict[str, Any]:
        cleaned = _strip_js_comments(statement).strip()
        if cleaned.startswith("{"):
            parser = _Parser(statement)
            command = parser.value()
            parser.finish()
            if not isinstance(command, dict):
                raise MongoSyntaxError("El comando JSON debe ser un objeto")
            collection, method, args, chain = self._command_from_json(command)
        elif cleaned.startswith("db"):
            parser = _Parser(statement)
            collection, method, args, chain = self._shell_command(parser)
        else:
            raise MongoSyntaxError(
                "El comando debe comenzar con 'db.' o ser un objeto JSON con 'action'."
            )
        return self._dispatch(db, collection, method, args, chain, statement)

    def _shell_command(self, parser: _Parser) -> tuple[str | None, str, list[Any], list[tuple[str, list[Any]]]]:
        first = parser.identifier()
        if first != "db":
            parser.error("El comando debe comenzar con 'db.'")
        parser.expect(".")
        name = parser.identifier()

        if name == "getCollection":
            args = parser.arguments()
            if not args or not isinstance(args[0], str) or not args[0]:
                raise MongoSyntaxError("getCollection requiere el nombre de la colección")
            collection = args[0]
            parser.expect(".")
            method = parser.identifier()
            method_args = parser.arguments()
        else:
            parser.skip()
            if parser.pos < parser.length and parser.text[parser.pos] == ".":
                collection = name
                parser.pos += 1
                method = parser.identifier()
                method_args = parser.arguments()
            else:
                collection = None
                method = name
                method_args = parser.arguments()

        chain: list[tuple[str, list[Any]]] = []
        while True:
            parser.skip()
            if parser.pos < parser.length and parser.text[parser.pos] == ".":
                parser.pos += 1
                chain_method = parser.identifier()
                chain_args = parser.arguments()
                chain.append((chain_method, chain_args))
                continue
            break
        parser.finish()
        return collection, method, method_args, chain

    def _command_from_json(
        self, command: dict[str, Any]
    ) -> tuple[str | None, str, list[Any], list[tuple[str, list[Any]]]]:
        def pick(*keys: str, default: Any = None) -> Any:
            for key in keys:
                if key in command:
                    return command[key]
            return default

        raw_action = pick("action", "operation", "method", "accion", "operacion")
        if not isinstance(raw_action, str):
            raise MongoSyntaxError(
                "El comando JSON necesita la propiedad 'action' (por ejemplo \"find\")."
            )
        action_key = re.sub(r"[_\-\s]", "", raw_action).lower()
        method = _ACTION_ALIASES.get(action_key)
        if method is None:
            supported = ", ".join(sorted(set(_ACTION_ALIASES.values())))
            raise MongoSyntaxError(
                f"Acción '{raw_action}' no soportada. Usa: {supported}"
            )

        collection = pick("collection", "coleccion", "name", "nombre")
        if collection is not None and not isinstance(collection, str):
            raise MongoSyntaxError("'collection' debe ser una cadena")

        filter_value = pick("filter", "filtro", default={})
        projection = pick("projection", "proyeccion")
        options = pick("options", "opciones", default={})
        if not isinstance(options, dict):
            raise MongoSyntaxError("'options' debe ser un objeto")

        chain: list[tuple[str, list[Any]]] = []
        if "sort" in command or "orden" in command:
            chain.append(("sort", [pick("sort", "orden")]))
        if "skip" in command:
            chain.append(("skip", [command["skip"]]))
        if "limit" in command or "limite" in command:
            chain.append(("limit", [pick("limit", "limite")]))

        if method == "createCollection":
            return (
                collection,
                method,
                [pick("collection", "name", "nombre"), options],
                [],
            )
        if method in {"dropDatabase", "listCollections"}:
            return None, method, [], []
        if method == "insertOne":
            return collection, method, [pick("document", "doc", "data", "documento")], []
        if method == "insertMany":
            return collection, method, [pick("documents", "docs", "documentos")], []
        if method in {"find", "findOne"}:
            return collection, method, [filter_value, projection], chain
        if method == "aggregate":
            return collection, method, [pick("pipeline", "etapas", default=[])], []
        if method == "countDocuments":
            return collection, method, [filter_value], []
        if method == "distinct":
            return collection, method, [pick("field", "campo"), filter_value], []
        if method in {"updateOne", "updateMany"}:
            if "upsert" in command and "upsert" not in options:
                options = {**options, "upsert": bool(command["upsert"])}
            return (
                collection,
                method,
                [filter_value, pick("update", "actualizacion"), options],
                [],
            )
        if method == "replaceOne":
            if "upsert" in command and "upsert" not in options:
                options = {**options, "upsert": bool(command["upsert"])}
            return (
                collection,
                method,
                [filter_value, pick("replacement", "reemplazo"), options],
                [],
            )
        if method in {"deleteOne", "deleteMany"}:
            return collection, method, [filter_value], []
        if method == "createIndex":
            return collection, method, [pick("keys", "index", "indice"), options], []
        if method == "drop":
            return collection, method, [], []

        raise MongoSyntaxError(f"Comando JSON no implementado: {method}")

    # ------------------------------------------------------------------
    # Dispatcher de operaciones
    # ------------------------------------------------------------------
    def _dispatch(
        self,
        db: Database,
        collection: str | None,
        method: str,
        args: list[Any],
        chain: list[tuple[str, list[Any]]],
        statement: str,
    ) -> dict[str, Any]:
        method = _ACTION_ALIASES.get(
            re.sub(r"[_\-\s]", "", method).lower(),
            method,
        )

        if method == "createCollection":
            name = args[0] if args else collection
            if not isinstance(name, str) or not name:
                raise MongoDriverError(
                    "createCollection requiere el nombre de la colección.",
                    error_type="query",
                    status_code=400,
                    statement=statement,
                )
            options = args[1] if len(args) > 1 and isinstance(args[1], dict) else {}
            allowed = {
                "capped", "size", "max", "validator", "validationLevel",
                "validationAction", "collation", "comment", "timeseries",
                "expireAfterSeconds", "clusteredIndex", "viewOn", "pipeline",
                "changeStreamPreAndPostImages",
            }
            filtered_options = {key: value for key, value in options.items() if key in allowed}
            db.create_collection(name, **filtered_options)
            return {
                "type": "command",
                "operation": "createCollection",
                "collection": name,
                "message": f"Colección '{name}' creada correctamente.",
                "stats": {"Colección": name},
                "statement": statement.strip(),
            }

        if method == "dropDatabase":
            db.client.drop_database(db.name)
            return {
                "type": "command",
                "operation": "dropDatabase",
                "collection": None,
                "message": f"Base de datos '{db.name}' eliminada correctamente.",
                "stats": {"Base de datos": db.name},
                "statement": statement.strip(),
            }

        if method == "listCollections":
            names = db.list_collection_names()
            return {
                "type": "value",
                "operation": "listCollections",
                "collection": None,
                "value": names,
                "message": f"{len(names)} colección(es) en '{db.name}'.",
                "statement": statement.strip(),
            }

        if method not in _COLLECTION_METHODS:
            supported = ", ".join(sorted(_COLLECTION_METHODS | {"createCollection", "dropDatabase", "listCollections"}))
            raise MongoDriverError(
                f"Operación '{method}' no soportada. Operaciones disponibles: {supported}",
                error_type="query",
                status_code=400,
                statement=statement,
            )

        if not collection:
            raise MongoDriverError(
                f"La operación {method} requiere indicar una colección: db.coleccion.{method}(...).",
                error_type="query",
                status_code=400,
                statement=statement,
            )

        if method == "drop":
            db.drop_collection(collection)
            return {
                "type": "command",
                "operation": "drop",
                "collection": collection,
                "message": f"Colección '{collection}' eliminada correctamente.",
                "stats": {"Colección": collection},
                "statement": statement.strip(),
            }

        collection_obj = db[collection]

        if method == "createIndex":
            keys = args[0] if args else None
            options = args[1] if len(args) > 1 and isinstance(args[1], dict) else {}
            if isinstance(keys, dict):
                keys = list(keys.items())
            if not isinstance(keys, (str, list)):
                raise MongoDriverError(
                    "createIndex requiere un campo o un objeto de claves.",
                    error_type="query",
                    status_code=400,
                    statement=statement,
                )
            index_name = collection_obj.create_index(keys, **options)
            return {
                "type": "command",
                "operation": "createIndex",
                "collection": collection,
                "message": f"Índice '{index_name}' creado correctamente.",
                "stats": {"Índice": index_name},
                "statement": statement.strip(),
            }

        if method == "insertOne":
            document = args[0] if args else None
            if not isinstance(document, dict):
                raise MongoDriverError(
                    "insertOne requiere un documento JSON, por ejemplo db.c.insertOne({nombre: 'Ana'}).",
                    error_type="query",
                    status_code=400,
                    statement=statement,
                )
            result = collection_obj.insert_one(document)
            return self._write_result(
                method, collection, statement,
                message="Documento insertado correctamente.",
                stats={"inserted_id": str(result.inserted_id), "acknowledged": result.acknowledged},
            )

        if method == "insertMany":
            documents = args[0] if args else None
            if not isinstance(documents, list) or not documents:
                raise MongoDriverError(
                    "insertMany requiere un arreglo con al menos un documento.",
                    error_type="query",
                    status_code=400,
                    statement=statement,
                )
            if len(documents) > 1000:
                raise MongoDriverError(
                    "insertMany admite como máximo 1000 documentos por ejecución en este IDE.",
                    error_type="validation",
                    status_code=422,
                    statement=statement,
                )
            if not all(isinstance(document, dict) for document in documents):
                raise MongoDriverError(
                    "Todos los elementos de insertMany deben ser objetos.",
                    error_type="query",
                    status_code=400,
                    statement=statement,
                )
            result = collection_obj.insert_many(documents)
            return self._write_result(
                method, collection, statement,
                message=f"{len(result.inserted_ids)} documentos insertados correctamente.",
                stats={
                    "inserted_count": len(result.inserted_ids),
                    "inserted_ids": [str(item) for item in result.inserted_ids],
                    "acknowledged": result.acknowledged,
                },
            )

        if method in {"find", "findOne"}:
            filter_value = args[0] if args else {}
            projection = args[1] if len(args) > 1 else None
            if filter_value is None:
                filter_value = {}
            if not isinstance(filter_value, dict):
                raise MongoDriverError(
                    "El filtro debe ser un objeto, por ejemplo {edad: {$gt: 18}}.",
                    error_type="query",
                    status_code=400,
                    statement=statement,
                )
            if projection is not None and not isinstance(projection, dict):
                raise MongoDriverError(
                    "La proyección debe ser un objeto, por ejemplo {nombre: 1, _id: 0}.",
                    error_type="query",
                    status_code=400,
                    statement=statement,
                )
            if method == "findOne":
                document = collection_obj.find_one(filter_value, projection)
                documents = [document] if document is not None else []
                return self._documents_result(
                    method, collection, statement, documents,
                    total=len(documents), limit=1, truncated=False,
                )

            limit = self._default_limit
            skip = 0
            sort_value = None
            count_only = False
            for chain_method, chain_args in chain:
                key = chain_method.lower()
                if key == "limit":
                    limit = self._as_int(chain_args[0] if chain_args else None, "limit")
                elif key == "skip":
                    skip = self._as_int(chain_args[0] if chain_args else None, "skip")
                elif key == "sort":
                    sort_value = chain_args[0] if chain_args else None
                elif key in {"pretty", "toarray"}:
                    continue
                elif key in {"count", "countdocuments"}:
                    count_only = True
                else:
                    raise MongoDriverError(
                        f"Método encadenado '.{chain_method}()' no soportado en find().",
                        error_type="query",
                        status_code=400,
                        hint="Se admiten .sort(), .skip(), .limit(), .count() y .pretty().",
                        statement=statement,
                    )

            if count_only:
                count = collection_obj.count_documents(filter_value)
                return {
                    "type": "value",
                    "operation": "countDocuments",
                    "collection": collection,
                    "value": count,
                    "message": f"{count} documento(s) coinciden con el filtro.",
                    "statement": statement.strip(),
                }

            limit = max(1, min(limit, self._hard_limit))
            skip = max(0, skip)
            cursor = collection_obj.find(filter_value, projection)
            if sort_value is not None:
                if isinstance(sort_value, dict):
                    cursor = cursor.sort(list(sort_value.items()))
                elif isinstance(sort_value, list):
                    cursor = cursor.sort(sort_value)
                else:
                    raise MongoDriverError(
                        "sort() requiere un objeto de campos y direcciones.",
                        error_type="query",
                        status_code=400,
                        statement=statement,
                    )
            if skip:
                cursor = cursor.skip(skip)
            cursor = cursor.limit(limit)
            raw_documents = list(cursor)
            documents = [_json_safe(document) for document in raw_documents]
            total = collection_obj.count_documents(filter_value)
            return self._documents_result(
                method, collection, statement, documents,
                total=total, limit=limit, truncated=total > skip + len(documents),
            )

        if method == "aggregate":
            pipeline = args[0] if args else None
            if not isinstance(pipeline, list):
                raise MongoDriverError(
                    "aggregate requiere un arreglo de etapas, por ejemplo [{$match: {...}}].",
                    error_type="query",
                    status_code=400,
                    statement=statement,
                )
            cursor = collection_obj.aggregate(pipeline)
            raw_documents = list(islice(cursor, self._hard_limit + 1))
            truncated = len(raw_documents) > self._hard_limit
            documents = [_json_safe(document) for document in raw_documents[: self._hard_limit]]
            return self._documents_result(
                method, collection, statement, documents,
                total=len(documents), limit=self._hard_limit, truncated=truncated,
            )

        if method == "countDocuments":
            filter_value = args[0] if args else {}
            if filter_value is None:
                filter_value = {}
            if not isinstance(filter_value, dict):
                raise MongoDriverError(
                    "countDocuments requiere un filtro objeto.",
                    error_type="query",
                    status_code=400,
                    statement=statement,
                )
            count = collection_obj.count_documents(filter_value)
            return {
                "type": "value",
                "operation": "countDocuments",
                "collection": collection,
                "value": count,
                "message": f"{count} documento(s) coinciden con el filtro.",
                "statement": statement.strip(),
            }

        if method == "distinct":
            field = args[0] if args else None
            filter_value = args[1] if len(args) > 1 else {}
            if not isinstance(field, str) or not field:
                raise MongoDriverError(
                    "distinct requiere el nombre del campo: db.c.distinct('campo').",
                    error_type="query",
                    status_code=400,
                    statement=statement,
                )
            if filter_value is None:
                filter_value = {}
            if not isinstance(filter_value, dict):
                raise MongoDriverError(
                    "El filtro de distinct debe ser un objeto.",
                    error_type="query",
                    status_code=400,
                    statement=statement,
                )
            values = [_json_safe(item) for item in collection_obj.distinct(field, filter_value)]
            return {
                "type": "value",
                "operation": "distinct",
                "collection": collection,
                "value": values,
                "message": f"{len(values)} valor(es) distintos en '{field}'.",
                "statement": statement.strip(),
            }

        if method in {"updateOne", "updateMany", "replaceOne"}:
            filter_value = args[0] if args else {}
            update_value = args[1] if len(args) > 1 else None
            options = args[2] if len(args) > 2 and isinstance(args[2], dict) else {}
            if not isinstance(filter_value, dict):
                raise MongoDriverError(
                    "El filtro debe ser un objeto.",
                    error_type="query",
                    status_code=400,
                    statement=statement,
                )
            if not isinstance(update_value, dict) or not update_value:
                label = "reemplazo" if method == "replaceOne" else "update"
                raise MongoDriverError(
                    f"{method} requiere un documento de {label}.",
                    error_type="query",
                    status_code=400,
                    statement=statement,
                )
            upsert = bool(options.get("upsert", False))
            if method == "updateOne":
                result = collection_obj.update_one(filter_value, update_value, upsert=upsert)
            elif method == "updateMany":
                result = collection_obj.update_many(filter_value, update_value, upsert=upsert)
            else:
                result = collection_obj.replace_one(filter_value, update_value, upsert=upsert)
            stats = {
                "matched_count": result.matched_count,
                "modified_count": result.modified_count,
                "upserted_id": str(result.upserted_id) if result.upserted_id is not None else None,
                "acknowledged": result.acknowledged,
            }
            message = (
                f"{result.matched_count} documento(s) coincidente(s), "
                f"{result.modified_count} modificado(s)."
            )
            return self._write_result(method, collection, statement, message=message, stats=stats)

        if method in {"deleteOne", "deleteMany"}:
            filter_value = args[0] if args else None
            if not isinstance(filter_value, dict):
                raise MongoDriverError(
                    f"{method} requiere un filtro objeto. Usa {{}} solo si realmente deseas borrar todo.",
                    error_type="query",
                    status_code=400,
                    statement=statement,
                )
            if method == "deleteOne":
                result = collection_obj.delete_one(filter_value)
            else:
                result = collection_obj.delete_many(filter_value)
            return self._write_result(
                method, collection, statement,
                message=f"{result.deleted_count} documento(s) eliminado(s).",
                stats={"deleted_count": result.deleted_count, "acknowledged": result.acknowledged},
            )

        raise MongoDriverError(
            f"Operación '{method}' no implementada.",
            error_type="query",
            status_code=400,
            statement=statement,
        )

    # ------------------------------------------------------------------
    # Constructores de resultados
    # ------------------------------------------------------------------
    @staticmethod
    def _as_int(value: Any, label: str) -> int:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise MongoDriverError(
                f"El argumento de {label} debe ser un número entero.",
                error_type="query",
                status_code=400,
            )
        return int(value)

    @staticmethod
    def _documents_result(
        method: str,
        collection: str,
        statement: str,
        documents: list[dict[str, Any]],
        *,
        total: int,
        limit: int,
        truncated: bool,
    ) -> dict[str, Any]:
        return {
            "type": "documents",
            "operation": method,
            "collection": collection,
            "documents": documents,
            "columns": _document_columns(documents),
            "count": len(documents),
            "total": total,
            "limit": limit,
            "truncated": truncated,
            "message": f"{len(documents)} documento(s) retornado(s).",
            "statement": statement.strip(),
        }

    @staticmethod
    def _write_result(
        method: str,
        collection: str,
        statement: str,
        *,
        message: str,
        stats: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            "type": "write",
            "operation": method,
            "collection": collection,
            "message": message,
            "stats": {key: _json_safe(value) for key, value in stats.items()},
            "statement": statement.strip(),
        }

    # ------------------------------------------------------------------
    # Traducción de errores de ejecución
    # ------------------------------------------------------------------
    def _translate_execution_error(
        self, exc: PyMongoError, *, statement: str, position: int
    ) -> MongoDriverError:
        prefix = f"Comando {position + 1}: " if position else ""
        text = str(exc).strip()

        if isinstance(exc, DuplicateKeyError) or (
            isinstance(exc, (WriteError, OperationFailure)) and getattr(exc, "code", None) == 11000
        ):
            return MongoDriverError(
                f"{prefix}Ya existe un documento con ese valor en un campo único.",
                error_type="conflict",
                status_code=409,
                detail=text,
                hint="Cambia el valor duplicado o elimina el índice único que lo restringe.",
                code=11000,
                statement=statement,
            )
        if isinstance(exc, OperationFailure):
            code = getattr(exc, "code", None)
            if code == 48:
                return MongoDriverError(
                    f"{prefix}La colección ya existe.",
                    error_type="conflict",
                    status_code=409,
                    detail=text,
                    hint="Usa otro nombre o elimina la colección antes de crearla.",
                    code=code,
                    statement=statement,
                )
            if code == 13:
                return MongoDriverError(
                    f"{prefix}El usuario de MongoDB no tiene permisos para esta operación.",
                    error_type="permission",
                    status_code=403,
                    detail=text,
                    hint="Revisa los roles del usuario en la base de datos.",
                    code=code,
                    statement=statement,
                )
            if code == 18:
                return MongoDriverError(
                    f"{prefix}Autenticación fallida en MongoDB.",
                    error_type="auth",
                    status_code=401,
                    detail=text,
                    code=code,
                    statement=statement,
                )
            details = getattr(exc, "details", None) or {}
            detail_text = details.get("errmsg") if isinstance(details, dict) else None
            return MongoDriverError(
                f"{prefix}MongoDB rechazó la operación.",
                error_type="query",
                status_code=400,
                detail=detail_text or text,
                hint="Revisa la sintaxis del filtro, del update o de las etapas de agregación.",
                code=code,
                statement=statement,
            )
        if isinstance(exc, (ServerSelectionTimeoutError, ConnectionFailure, AutoReconnect)):
            return MongoDriverError(
                f"{prefix}Se perdió la conexión con MongoDB.",
                error_type="connection",
                status_code=503,
                detail=text,
                hint="Verifica que mongod siga en ejecución y vuelve a conectar.",
                statement=statement,
            )
        if isinstance(exc, WriteError):
            return MongoDriverError(
                f"{prefix}Error de escritura en MongoDB.",
                error_type="query",
                status_code=400,
                detail=text,
                code=getattr(exc, "code", None),
                statement=statement,
            )
        return MongoDriverError(
            f"{prefix}Error al ejecutar el comando en MongoDB.",
            error_type="database",
            status_code=500,
            detail=text,
            statement=statement,
        )


# Instancia única compartida por los routers.
mongo_driver = MongoDriver()
