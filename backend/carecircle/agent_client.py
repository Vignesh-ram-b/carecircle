"""Invoke the CareCircle agent hosted on Amazon Bedrock AgentCore Runtime."""

import json

import boto3
from botocore.config import Config

from . import config


def session_id_for(key: str) -> str:
    # AgentCore requires runtimeSessionId to be at least 33 characters.
    sid = f"carecircle-{key}"
    return sid if len(sid) >= 33 else sid.ljust(33, "0")


def invoke(event: dict, session_key: str) -> dict:
    arn = config.agent_runtime_arn()
    if not arn:
        raise RuntimeError("AgentCore runtime ARN is not configured")
    client = boto3.client(
        "bedrock-agentcore", region_name=config.region(),
        config=Config(read_timeout=120, connect_timeout=10, retries={"max_attempts": 1}),
    )
    resp = client.invoke_agent_runtime(
        agentRuntimeArn=arn,
        runtimeSessionId=session_id_for(session_key),
        payload=json.dumps({"event": event}).encode("utf-8"),
    )
    body = resp["response"].read() if hasattr(resp.get("response"), "read") else resp.get("response")
    if isinstance(body, bytes):
        body = body.decode("utf-8", errors="replace")
    try:
        return json.loads(body)
    except (TypeError, json.JSONDecodeError):
        return {"summary": str(body)}
