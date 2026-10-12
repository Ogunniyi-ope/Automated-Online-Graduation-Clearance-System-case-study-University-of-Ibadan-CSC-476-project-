"""Entry point:  flask --app wsgi run   (development)   |   gunicorn wsgi:app   (production)."""
from clearance import create_app

app = create_app()
