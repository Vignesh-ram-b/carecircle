#!/usr/bin/env python3
import hashlib
import json
import os
import sys

import aws_cdk as cdk

from carecircle_stack import CareCircleStack

HERE = os.path.dirname(os.path.abspath(__file__))
config_path = os.path.join(HERE, "config.json")
if not os.path.exists(config_path):
    sys.exit("infra/config.json not found - copy infra/config.example.json and fill it in.")

with open(config_path, encoding="utf-8") as fh:
    cfg = json.load(fh)
if not cfg.get("apiToken") or cfg["apiToken"].startswith("CHANGE-ME"):
    sys.exit("Set a random apiToken in infra/config.json (e.g. `python -c 'import secrets;print(secrets.token_urlsafe(24))'`).")

# Secret for signing the one-tap email buttons (derived, so there is nothing extra to configure).
cfg["actionSecret"] = hashlib.sha256(("carecircle-actions:" + cfg["apiToken"]).encode()).hexdigest()

if not os.path.isdir(os.path.join(HERE, "..", "build", "boto3-layer", "python")):
    sys.exit("Lambda layer not built - run ./scripts/build_layer.sh from the repo root first.")

app = cdk.App()
CareCircleStack(
    app, "CareCircle", cfg=cfg,
    env=cdk.Environment(account=os.environ.get("CDK_DEFAULT_ACCOUNT"),
                        region=cfg.get("region") or os.environ.get("CDK_DEFAULT_REGION")),
    description="CareCircle - agentic family health alerts on AWS CDS (WhatsApp, SMS, SES) + Bedrock AgentCore",
)
app.synth()
