"""WSGI-точка входа контейнера инференса.

gunicorn запускает её так же, как монолитный ``wsgi.py``: один воркер (веса
живут в его процессе), несколько потоков (SSE-поток держит GPU, /healthz
обязан отвечать в это время).
"""

from inference.app import create_app

app = create_app()
