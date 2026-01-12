from contextlib import asynccontextmanager
import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from loguru import logger
from fastapi import Request, Response
from fastapi.routing import APIRoute

from api.auth import JWTAuthMiddleware
from api.routers import (
    auth,
    chat,
    config,
    context,
    embedding,
    embedding_rebuild,
    episode_profiles,
    insights,
    models,
    notebooks,
    notes,
    podcasts,
    search,
    settings,
    source_chat,
    sources,
    speaker_profiles,
    transformations,
    infra,
)
from api.routers import commands as commands_router
from open_notebook.database.async_migrate import AsyncMigrationManager

# Import commands to register them in the API process
try:

    logger.info("Commands imported in API process")
except Exception as e:
    logger.error(f"Failed to import commands in API process: {e}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Lifespan event handler for the FastAPI application.
    Runs database migrations automatically on startup.
    """
    if os.environ.get("SKIP_MIGRATIONS_FOR_TESTS"):
        yield
        return
    # Startup: Run database migrations
    logger.info("Starting API initialization...")

    try:
        migration_manager = AsyncMigrationManager()
        current_version = await migration_manager.get_current_version()
        logger.info(f"Current database version: {current_version}")

        if await migration_manager.needs_migration():
            logger.warning("Database migrations are pending. Running migrations...")
            await migration_manager.run_migration_up()
            new_version = await migration_manager.get_current_version()
            logger.success(f"Migrations completed successfully. Database is now at version {new_version}")
        else:
            logger.info("Database is already at the latest version. No migrations needed.")
    except Exception as e:
        # Do not crash the API if the DB is offline; the infra endpoints must stay available
        logger.error(f"Database migration failed (DB may be offline): {str(e)}")
        logger.exception(e)
        logger.warning("Continuing API startup so the UI can offer a 'Start server' action.")

    logger.success("API initialization completed successfully")

    # Yield control to the application
    yield

    # Shutdown: cleanup if needed
    logger.info("API shutdown complete")


app = FastAPI(
    title="Open Notebook API",
    description="API for Open Notebook - Research Assistant",
    version="0.2.2",
    lifespan=lifespan,
)


class LoggingRoute(APIRoute):
    """
    Route wrapper that emits ultra‑verbose per‑endpoint diagnostics.
    This complements the HTTP middleware by logging after dependency
    resolution and before the underlying handler returns.
    """

    def get_route_handler(self):
        original_route_handler = super().get_route_handler()

        async def logging_route_handler(request: Request):
            logger.debug(
                "ROUTE START path=%s method=%s user=%s client=%s route_name=%s",
                request.url.path,
                request.method,
                getattr(request.state, "user_id", None),
                getattr(request.client, "host", None),
                self.name,
            )
            response: Response = await original_route_handler(request)
            logger.debug(
                "ROUTE END path=%s method=%s status=%s headers=%s",
                request.url.path,
                request.method,
                getattr(response, "status_code", None),
                dict(response.headers),
            )
            return response

        return logging_route_handler


# Apply the logging route to all routers
app.router.route_class = LoggingRoute

# Add JWT authentication middleware first
# Exclude auth + config endpoints + infra controls (needed before DB is up)
app.add_middleware(
    JWTAuthMiddleware,
    excluded_paths=[
        "/",
        "/health",
        "/docs",
        "/openapi.json",
        "/redoc",
        "/api/auth/status",
        "/api/auth/login/google",
        "/api/config",
        "/api/infra/db-vm/status",
        "/api/infra/db-vm/start",
        "/api/infra/db-vm/stop",
    ],
)

# Add CORS middleware last (so it processes first)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # In production, replace with specific origins
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include routers
app.include_router(auth.router, prefix="/api", tags=["auth"])
app.include_router(config.router, prefix="/api", tags=["config"])
app.include_router(notebooks.router, prefix="/api", tags=["notebooks"])
app.include_router(search.router, prefix="/api", tags=["search"])
app.include_router(models.router, prefix="/api", tags=["models"])
app.include_router(transformations.router, prefix="/api", tags=["transformations"])
app.include_router(notes.router, prefix="/api", tags=["notes"])
app.include_router(embedding.router, prefix="/api", tags=["embedding"])
app.include_router(embedding_rebuild.router, prefix="/api/embeddings", tags=["embeddings"])
app.include_router(settings.router, prefix="/api", tags=["settings"])
app.include_router(context.router, prefix="/api", tags=["context"])
app.include_router(infra.router, prefix="/api", tags=["infra"])
app.include_router(sources.router, prefix="/api", tags=["sources"])
app.include_router(insights.router, prefix="/api", tags=["insights"])
app.include_router(commands_router.router, prefix="/api", tags=["commands"])
app.include_router(podcasts.router, prefix="/api", tags=["podcasts"])
app.include_router(episode_profiles.router, prefix="/api", tags=["episode-profiles"])
app.include_router(speaker_profiles.router, prefix="/api", tags=["speaker-profiles"])
app.include_router(chat.router, prefix="/api", tags=["chat"])
app.include_router(source_chat.router, prefix="/api", tags=["source-chat"])


@app.middleware("http")
async def log_requests(request: Request, call_next):
    import time
    start_time = time.time()
    try:
        body = await request.body()
    except Exception:
        body = b""
    logger.info(
        "HTTP START %s %s headers=%s body=%s",
        request.method,
        request.url.path,
        dict(request.headers),
        body[:2000],
    )
    response: Response = await call_next(request)
    duration = (time.time() - start_time) * 1000
    logger.info(
        "HTTP END %s %s status=%s duration_ms=%.2f",
        request.method,
        request.url.path,
        response.status_code,
        duration,
    )
    return response


@app.get("/")
async def root():
    return {"message": "Open Notebook API is running"}


@app.get("/health")
async def health():
    return {"status": "healthy"}
