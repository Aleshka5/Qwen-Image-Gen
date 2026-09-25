"""Точка входа для gunicorn: ``gunicorn wsgi:app``."""

from app import create_app

app = create_app()

if __name__ == "__main__":
    from app.config import settings

    app.run(host=settings.host, port=settings.port, debug=False, threaded=True)
