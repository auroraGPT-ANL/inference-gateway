#!/bin/sh
#
# A simple script that will wait for a TCP port to be available.
#
# Author: J-Pal http://j-pal.github.io/
#
# Gist: https://gist.github.com/j-pal/9c6e4359483344898144

set -e

HOST=$1
PORT=$2
shift 2
CMD="$@"
TRIES=0
MAX_TRIES=15

echo "Waiting for $HOST:$PORT to be available..."

while ! nc -z "$HOST" "$PORT" >/dev/null 2>&1; do
  TRIES=$((TRIES+1))
  if [ $TRIES -gt $MAX_TRIES ]; then
    echo "ERROR: $HOST:$PORT is not available after $MAX_TRIES tries"
    exit 1
  fi
  sleep 1
done

echo "$HOST:$PORT is available. Executing command: $CMD"
exec $CMD 