"""Endpoints REST para MongoDB."""

from __future__ import annotations

import logging

from fastapi import APIRouter, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from database.nosql_driver import MongoDriverError, mongo_driver

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/nosql", tags=["MongoDB"])


class MongoConnectRequest(BaseModel):
    uri: str = Field(min_length=1, max_length=2000)
    database: str | None = Field(default=None, max_length=128)


class MongoExecuteRequest(BaseModel):
    command: str = Field(min_length=1, max_length=200_000)


def _error_response(exc: MongoDriverError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={"success": False, "error": exc.to_dict()},
    )


@router.post("/connect")
def connect(payload: MongoConnectRequest) -> JSONResponse:
    """Prueba la URI y guarda la conexión activa."""
    try:
        details = mongo_driver.connect(uri=payload.uri, database=payload.database)
    except MongoDriverError as exc:
        return _error_response(exc)
    except Exception as exc:
        logger.exception("Error inesperado en /api/nosql/connect")
        return JSONResponse(
            status_code=500,
            content={
                "success": False,
                "error": {
                    "type": "server",
                    "message": "Error inesperado al conectar con MongoDB.",
                    "detail": f"{type(exc).__name__}: {exc}",
                },
            },
        )
    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content={
            "success": True,
            "message": "Conexión establecida con MongoDB.",
            "connection": details,
        },
    )


@router.post("/disconnect")
def disconnect() -> dict:
    """Cierra el cliente de MongoDB (idempotente)."""
    mongo_driver.disconnect()
    return {"success": True, "message": "Conexión con MongoDB cerrada."}


@router.get("/status")
def mongo_status() -> dict:
    return {"success": True, "connection": mongo_driver.status()}


@router.post("/execute")
def execute(payload: MongoExecuteRequest) -> JSONResponse:
    """Ejecuta comandos del shell de MongoDB o un comando JSON."""
    try:
        result = mongo_driver.execute(payload.command)
    except MongoDriverError as exc:
        return _error_response(exc)
    except Exception as exc:
        logger.exception("Error inesperado en /api/nosql/execute")
        return JSONResponse(
            status_code=500,
            content={
                "success": False,
                "error": {
                    "type": "server",
                    "message": "Error inesperado al ejecutar el comando de MongoDB.",
                    "detail": f"{type(exc).__name__}: {exc}",
                },
            },
        )
    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content={
            "success": True,
            "engine": "mongodb",
            "duration_ms": result["duration_ms"],
            "statement_count": result["statement_count"],
            "results": result["results"],
        },
    )
