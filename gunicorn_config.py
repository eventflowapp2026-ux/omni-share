# Gunicorn configuration
workers = 4
worker_class = 'sync'
worker_connections = 1000
timeout = 30
keepalive = 2
