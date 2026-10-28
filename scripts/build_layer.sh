#!/usr/bin/env bash
# Builds the Lambda layer with a recent boto3 (pure Python, no Docker needed),
# so the socialmessaging and bedrock-agentcore clients are always available.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
rm -rf "$ROOT/build/boto3-layer"
mkdir -p "$ROOT/build/boto3-layer/python"
python3 -m pip install --quiet --upgrade "boto3>=1.40.0" -t "$ROOT/build/boto3-layer/python"
echo "boto3 layer built in build/boto3-layer"
