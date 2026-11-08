#!/bin/sh
# Build the sandbox image. Run from anywhere.
# The Dockerfile itself fails the build if solc is in the runtime image or the artifact is missing.
set -eu
cd "$(dirname "$0")"
docker build -t fail-closed-sandbox:foundry-v1.8.5 .
echo "built fail-closed-sandbox:foundry-v1.8.5"
