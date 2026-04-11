from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError

from errors import ApiError, api_error_exception_handler, validation_exception_handler
from routes.generate import router as generate_router
from structured_logging import configure_logging


def create_app() -> FastAPI:
    configure_logging()
    app = FastAPI(title="luaMTS API")
    app.add_exception_handler(ApiError, api_error_exception_handler)
    app.add_exception_handler(RequestValidationError, validation_exception_handler)

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    app.include_router(generate_router)

    return app


app = create_app()
