from fastapi import FastAPI


def create_app() -> FastAPI:
    app = FastAPI(title="ChatDBA", version="0.1.0")

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok", "service": "chatdba"}

    return app


app = create_app()
