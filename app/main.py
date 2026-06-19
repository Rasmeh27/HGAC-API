"""Punto de entrada FastAPI del backend HGAC.

Levantar con:
    uvicorn app.main:app --reload
"""

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from loguru import logger

from app.api.routes import (
    biostar_routes,
    camera_routes,
    crossing_routes,
    health_routes,
    lpr_reads_routes,
    lpr_routes,
    monitor_routes,
    rntt_routes,
)
from app.core.config import Settings, get_settings
from app.core.errors import AppError, IntegrationError
from app.core.logging import configure_logging


@asynccontextmanager
async def _lifespan(app: FastAPI):
    settings = get_settings()
    logger.info("Backend HGAC iniciado en entorno '{}'", settings.app_env)
    yield
    logger.info("Backend HGAC detenido")


def create_app() -> FastAPI:
    configure_logging()
    settings = get_settings()

    app = FastAPI(
        title=settings.app_name,
        version="0.1.0",
        description=(
            "Backend PoC para control de cruce vehicular portuario HGAC. "
            "Integra LPR local/externo, BioStar 2, RNTT e Ignition."
        ),
        lifespan=_lifespan,
    )

    app.include_router(health_routes.router)
    app.include_router(lpr_routes.router)
    app.include_router(lpr_reads_routes.router)
    app.include_router(monitor_routes.router)
    app.include_router(biostar_routes.router)
    app.include_router(rntt_routes.router)
    app.include_router(crossing_routes.router)
    app.include_router(camera_routes.router)

    _mount_evidence_static(app, settings)
    _register_exception_handlers(app)

    return app


def _mount_evidence_static(app: FastAPI, settings: Settings) -> None:
    """Sirve la carpeta de evidencia para que Ignition (y el navegador) puedan
    abrir los snapshots por URL pública.

    `evidence_base_path` apunta al subdirectorio de snapshots (p.ej.
    ``./evidence/snapshots``); montamos su carpeta raíz (``evidence``) en
    ``/evidence`` para que las URLs queden como
    ``/evidence/snapshots/<filename>``.
    """
    evidence_root = Path(settings.evidence_base_path).parent
    evidence_root.mkdir(parents=True, exist_ok=True)
    app.mount(
        "/evidence",
        StaticFiles(directory=str(evidence_root)),
        name="evidence",
    )


def _register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(IntegrationError)
    async def _integration_error_handler(request: Request, exc: IntegrationError):
        logger.warning("IntegrationError en {}: {}", request.url.path, exc)
        return JSONResponse(
            status_code=502,
            content={"error": "integration_error", "detail": str(exc)},
        )

    @app.exception_handler(AppError)
    async def _app_error_handler(request: Request, exc: AppError):
        logger.error("AppError en {}: {}", request.url.path, exc)
        return JSONResponse(
            status_code=500,
            content={"error": "app_error", "detail": str(exc)},
        )


app = create_app()