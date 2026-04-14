#!/bin/bash
set -e

echo "Starting collector entrypoint..."

if [ -z "$INTERFACE" ]; then
    echo "Warning: INTERFACE not set, defaulting to wlan0"
    export INTERFACE="wlan0"
fi

echo "Interface: $INTERFACE"
echo "Database: $DATABASE_PATH"

echo "Testing interface availability..."
ip link show "$INTERFACE" || {
    echo "Error: Interface $INTERFACE not found"
    exit 1
}

echo "Starting collector..."
python -m app
