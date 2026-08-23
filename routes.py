"""Backward-compatible WSGI module.

The application factory in ``app.py`` is the canonical entry point. This file is
kept so an existing ``routes:app`` deployment target does not fail.
"""

from app import create_app

app = create_app()


if __name__ == "__main__":
    app.run(debug=True, threaded=True)
