import os

from waitress import serve

from app import create_app

if __name__ == "__main__":
    app = create_app()
    serve(
        app,
        host=os.getenv("FLASK_HOST", "0.0.0.0"),
        port=int(os.getenv("FLASK_PORT", "5000")),
    )
