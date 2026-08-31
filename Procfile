# Start command for Railway (and anything else that reads a Procfile).
#
# HOST is forced here as well as defaulted in config.py (which now treats the
# presence of $PORT as the signal to bind publicly) -- belt and braces, since
# this is the line that actually runs.
#
# It matters because a bare default of 127.0.0.1 is right on a laptop and
# wrong in a container: loopback is reachable only from inside the container,
# so the platform's proxy finds nothing listening and serves "Application
# failed to respond". PORT is injected by the platform, and config.py reads it.
#
# PYTHONUNBUFFERED keeps the startup banner and request log flowing into the
# platform's log viewer; without it Python buffers stdout when it is not a
# terminal and the logs look empty.
web: HOST=0.0.0.0 PYTHONUNBUFFERED=1 python3 app.py
