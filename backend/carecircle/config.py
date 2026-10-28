"""Runtime configuration.

Lambdas receive configuration as environment variables (set by CDK).
The AgentCore agent runs in its own container without those variables, so it
reads the same values from one SSM parameter (a JSON document written by CDK).
Environment variables always win over SSM, which keeps local testing simple.
"""

import json
import os
from functools import lru_cache

import boto3

CONFIG_PARAM_DEFAULT = "/carecircle/config"
AGENT_ARN_PARAM_DEFAULT = "/carecircle/agent-runtime-arn"

DEFAULTS = {
    "WA_META_API_VERSION": "v20.0",
    "WA_TEMPLATE_ALERT": "care_alert",
    "WA_TEMPLATE_LANG": "en",
    "MODEL_ID": "au.anthropic.claude-sonnet-4-5-20250929-v1:0",
    "ACK_TIMEOUT_MIN": "5",
    "OFFLINE_MINUTES": "0",
    "EMERGENCY_NUMBER": "108",
    "DRY_RUN": "false",
    "AGENT_ARN_PARAM": AGENT_ARN_PARAM_DEFAULT,
}


def region() -> str:
    return (
        os.environ.get("AWS_REGION")
        or os.environ.get("AWS_DEFAULT_REGION")
        or os.environ.get("CC_REGION")
        or "ap-southeast-2"
    )


@lru_cache(maxsize=1)
def _ssm_config() -> dict:
    name = os.environ.get("CC_CONFIG_PARAM", CONFIG_PARAM_DEFAULT)
    try:
        ssm = boto3.client("ssm", region_name=region())
        value = ssm.get_parameter(Name=name)["Parameter"]["Value"]
        return json.loads(value)
    except Exception as exc:  # noqa: BLE001 - config must never crash the caller
        print(f"[config] could not load SSM config {name}: {exc}")
        return {}


def get(key: str, default=None):
    if key in os.environ and os.environ[key] != "":
        return os.environ[key]
    cfg = _ssm_config() if not os.environ.get("CC_SKIP_SSM") else {}
    if key in cfg and cfg[key] not in (None, ""):
        return cfg[key]
    if default is not None:
        return default
    return DEFAULTS.get(key)


def get_int(key: str, default: int = 0) -> int:
    try:
        return int(get(key, str(default)))
    except (TypeError, ValueError):
        return default


def dry_run() -> bool:
    return str(get("DRY_RUN", "false")).lower() in ("1", "true", "yes")


def agent_runtime_arn():
    """The AgentCore runtime ARN is stored separately because it is created
    after the CDK stack (by the AgentCore starter toolkit)."""
    if os.environ.get("AGENT_RUNTIME_ARN"):
        return os.environ["AGENT_RUNTIME_ARN"]
    name = get("AGENT_ARN_PARAM", AGENT_ARN_PARAM_DEFAULT)
    try:
        ssm = boto3.client("ssm", region_name=region())
        value = ssm.get_parameter(Name=name)["Parameter"]["Value"].strip()
        return value if value.startswith("arn:") else None
    except Exception as exc:  # noqa: BLE001
        print(f"[config] agent runtime ARN not available ({name}): {exc}")
        return None
