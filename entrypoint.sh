#!/usr/bin/env sh
# Container entrypoint for the bot service.
#
# Model provisioning ($OLLAMA_MODEL pull-if-missing) and the APScheduler boot
# happen inside the FastAPI lifespan (app.runner.app:ensure_model). This wrapper
# only execs the passed command so PID 1 forwards signals (SIGTERM/SIGINT) to
# uvicorn for clean shutdown.
set -e

exec "$@"
