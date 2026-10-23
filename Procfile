web: daphne chatapp.asgi:application --port $PORT --bind 0.0.0.0
worker: celery -A chatapp worker -l info -P solo
beat: celery -A chatapp beat -l info