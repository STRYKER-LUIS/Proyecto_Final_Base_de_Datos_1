"""Endpoints REST para PostgreSQL."""

from __future__ import annotations

import logging

from fastapi import APIRouter, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from database.sql_driver import SqlDriverError, postgres_driver

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/sql", tags=["PostgreSQL"])


class SqlConnectRequest(BaseModel):
    host: str = Field(default="localhost", min_length=1, max_length=255)
    port: int = Field(default=5432, ge=1, le=65535)
    user: str = Field(min_length=1, max_length=128)
    password: str = Field(default="", max_length=512)
    dbname: str = Field(min_length=1, max_length=128)


class SqlExecuteRequest(BaseModel):
    query: str = Field(min_length=1, max_length=200_000)


def _error_response(exc: SqlDriverError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={"success": False, "error": exc.to_dict()},
    )


@router.post("/connect")
def connect(payload: SqlConnectRequest) -> JSONResponse:
    """Prueba las credenciales y guarda la conexión activa."""
    try:
        details = postgres_driver.connect(
            host=payload.host,
            port=payload.port,
            user=payload.user,
            password=payload.password,
            dbname=payload.dbname,
        )
    except SqlDriverError as exc:
        return _error_response(exc)
    except Exception as exc:  # defensa: nunca devolver una traza al navegador
        logger.exception("Error inesperado en /api/sql/connect")
        return JSONResponse(
            status_code=500,
            content={
                "success": False,
                "error": {
                    "type": "server",
                    "message": "Error inesperado al conectar con PostgreSQL.",
                    "detail": f"{type(exc).__name__}: {exc}",
                },
            },
        )
    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content={
            "success": True,
            "message": "Conexión establecida con PostgreSQL.",
            "connection": details,
        },
    )


@router.post("/disconnect")
def disconnect() -> dict:
    """Cierra la conexión activa (idempotente)."""
    postgres_driver.disconnect()
    return {"success": True, "message": "Conexión con PostgreSQL cerrada."}


@router.get("/status")
def sql_status() -> dict:
    return {"success": True, "connection": postgres_driver.status()}


@router.post("/execute")
def execute(payload: SqlExecuteRequest) -> JSONResponse:
    """Ejecuta una o varias sentencias SQL y devuelve resultados estructurados."""
    try:
        result = postgres_driver.execute(payload.query)
    except SqlDriverError as exc:
        return _error_response(exc)
    except Exception as exc:  # defensa: nunca devolver una traza al navegador
        logger.exception("Error inesperado en /api/sql/execute")
        return JSONResponse(
            status_code=500,
            content={
                "success": False,
                "error": {
                    "type": "server",
                    "message": "Error inesperado al ejecutar la consulta SQL.",
                    "detail": f"{type(exc).__name__}: {exc}",
                },
            },
        )
    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content={
            "success": True,
            "engine": "postgresql",
            "duration_ms": result["duration_ms"],
            "statement_count": result["statement_count"],
            "results": result["results"],
        },
    )
