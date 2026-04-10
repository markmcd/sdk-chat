#!/bin/bash
# run.sh - Entrypoint for Cloud Run job

# Run the main task (passed as arguments)
uv run app.py ingest --update --since 24h
MAIN_EXIT_CODE=$?

if [ $MAIN_EXIT_CODE -ne 0 ]; then
  echo "Main task failed with exit code $MAIN_EXIT_CODE. Skipping cleanup."
  exit $MAIN_EXIT_CODE
fi

# Run the cleanup task only if main task succeeded
echo "Running cleanup task: uv run clean --delete"
uv run clean --delete
CLEAN_EXIT_CODE=$?
exit $CLEAN_EXIT_CODE
