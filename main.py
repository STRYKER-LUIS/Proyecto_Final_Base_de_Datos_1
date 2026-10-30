"""Punto de entrada del Mini IDE de Base de Datos 1.

Arranque:
    uvicorn main:app --reload
o
    python main.py

La interfaz se sirve desde /static y la página principal en /.
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
load_dotenv(BASE_DIR / ".env")

# Los drivers se importan después de cargar .env para respetar los límites.
from database.nosql_driver import mongo_driver  # noqa: E402
from database.sql_driver import postgres_driver  # noqa: E402
from routers import nosql_router, sql_router  # noqa: E402


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


@asynccontextmanager
async def lifespan(_: FastAPI):
    yield
    # Cierre limpio al detener Uvicorn.
    postgres_driver.disconnect()
    mongo_driver.disconnect()


app = FastAPI(
    title="Mini IDE · Base de Datos 1",
    description=(
        "Editor y consola para ejecutar consultas PostgreSQL y comandos "
        "MongoDB desde el navegador."
    ),
    version="1.0.0",
    lifespan=lifespan,
)

_origins = [item.strip() for item in os.getenv("CORS_ORIGINS", "*").split(",") if item.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins or ["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(sql_router.router)
app.include_router(nosql_router.router)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    """Sirve la interfaz del mini IDE."""
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/status", tags=["Sistema"])
def global_status() -> dict:
    """Estado de ambas conexiones; lo usa la UI al cargar."""
    return {
        "success": True,
        "postgresql": postgres_driver.status(),
        "mongodb": mongo_driver.status(),
        "server_time": datetime.now().astimezone().isoformat(timespec="seconds"),
    }


@app.get("/api/config", tags=["Sistema"])
def client_config() -> dict:
    """Valores por defecto (sin secretos) para precargar los formularios."""
    return {
        "success": True,
        "sql": {
            "host": os.getenv("SQL_DEFAULT_HOST", "localhost"),
            "port": _env_int("SQL_DEFAULT_PORT", 5432),
            "user": os.getenv("SQL_DEFAULT_USER", "postgres"),
            "dbname": os.getenv("SQL_DEFAULT_DB", "postgres"),
        },
        "mongodb": {
            "uri": os.getenv("MONGO_DEFAULT_URI", "mongodb://localhost:27017"),
            "database": os.getenv("MONGO_DEFAULT_DB", "mini_ide_bd1"),
        },
        "limits": {
            "sql": _env_int("SQL_RESULT_LIMIT", 1000),
            "mongo": _env_int("MONGO_RESULT_LIMIT", 100),
        },
    }


@app.get("/api/health", tags=["Sistema"])
def health() -> dict:
    return {"success": True, "status": "ok"}


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
    """Normaliza los errores de validación de Pydantic al formato de la UI."""
    translations = {
        "missing": "El campo es obligatorio.",
        "string_too_short": "El texto es demasiado corto (o está vacío).",
        "string_too_long": "El texto supera la longitud máxima permitida.",
        "int_parsing": "Debe ser un número entero.",
        "int_type": "Debe ser un número entero.",
        "float_parsing": "Debe ser un número.",
        "greater_than_equal": "El valor es menor que el mínimo permitido.",
        "less_than_equal": "El valor supera el máximo permitido.",
        "json_invalid": "El cuerpo de la petición no es un JSON válido.",
        "bool_parsing": "Debe ser verdadero o falso.",
        "string_type": "Debe ser texto.",
    }
    details = []
    for error in exc.errors():
        location = ".".join(str(part) for part in error.get("loc", []) if part != "body")
        error_type = error.get("type", "validation_error")
        details.append(
            {
                "field": location or "cuerpo",
                "message": translations.get(error_type, error.get("msg", "Valor inválido")),
                "type": error_type,
            }
        )
    first = details[0] if details else {"field": "cuerpo", "message": "Datos inválidos"}
    return JSONResponse(
        status_code=422,
        content={
            "success": False,
            "error": {
                "type": "validation",
                "message": f"Datos inválidos en '{first['field']}': {first['message']}",
                "detail": details,
            },
        },
    )


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(_: Request, exc: StarletteHTTPException) -> JSONResponse:
    """Devuelve errores HTTP como JSON uniforme."""
    if isinstance(exc.detail, dict) and "error" in exc.detail:
        return JSONResponse(status_code=exc.status_code, content=exc.detail)
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "success": False,
            "error": {
                "type": "http",
                "message": str(exc.detail) if exc.detail else "Recurso no encontrado.",
            },
        },
    )


if __name__ == "__main__":
    import uvicorn

    reload_enabled = os.getenv("APP_RELOAD", "false").lower() in {"1", "true", "yes"}
    uvicorn.run(
        "main:app" if reload_enabled else app,
        host=os.getenv("APP_HOST", "127.0.0.1"),
        port=_env_int("APP_PORT", 8000),
        reload=reload_enabled,
    )
