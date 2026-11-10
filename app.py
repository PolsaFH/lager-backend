"""WSGI entry point for the Lager API.

Production (Passenger/shared hosting) imports ``application`` from here via
``passenger_wsgi.py``. For local development run ``python app.py``.
"""
from lager.api import App, configure_logging

configure_logging()
application = App()

if __name__ == "__main__":
    import os
    from wsgiref.simple_server import make_server

    port = int(os.environ.get("PORT", "8000"))
    print(f"Lager API på http://localhost:{port}  (Ctrl+C for å stoppe)")
    make_server("0.0.0.0", port, application).serve_forever()
