# Railway/Heroku start command. HOST is forced to 0.0.0.0 because the default
# in config.py is 127.0.0.1, which inside a container is only reachable from
# the container itself -- the platform's proxy sees nothing listening and
# serves "Application failed to respond". PORT is injected by the platform.
web: HOST=0.0.0.0 python3 app.py
