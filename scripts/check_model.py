#!/usr/bin/env python3
"""Find a Claude model that works in your account and region.

Usage:
    python3 scripts/check_model.py                 # uses region from infra/config.json
    python3 scripts/check_model.py --region ap-southeast-2

Prints which model IDs answer, and the one to put in infra/config.json -> "modelId".
Run the Bedrock console playground once first (Anthropic models need a one-time
use-case form).
"""

import argparse
import json
import os

import boto3

CANDIDATES = [
    # in-region / geographic profiles first, then global
    "au.anthropic.claude-sonnet-4-5-20250929-v1:0",
    "apac.anthropic.claude-sonnet-4-5-20250929-v1:0",
    "global.anthropic.claude-sonnet-4-5-20250929-v1:0",
    "us.anthropic.claude-sonnet-4-5-20250929-v1:0",
    "au.anthropic.claude-haiku-4-5-20251001-v1:0",
    "apac.anthropic.claude-haiku-4-5-20251001-v1:0",
    "global.anthropic.claude-haiku-4-5-20251001-v1:0",
    "us.anthropic.claude-haiku-4-5-20251001-v1:0",
    "apac.anthropic.claude-sonnet-4-20250514-v1:0",
    "apac.anthropic.claude-3-5-sonnet-20241022-v2:0",
    "anthropic.claude-3-5-sonnet-20240620-v1:0",
    "anthropic.claude-3-haiku-20240307-v1:0",
]


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    cfg_path = os.path.join(here, "..", "infra", "config.json")
    cfg = json.load(open(cfg_path, encoding="utf-8")) if os.path.exists(cfg_path) else {}
    ap = argparse.ArgumentParser()
    ap.add_argument("--region", default=cfg.get("region") or os.environ.get("AWS_REGION", "ap-southeast-2"))
    args = ap.parse_args()

    client = boto3.client("bedrock-runtime", region_name=args.region)
    print(f"Testing Claude models in {args.region} ...\n")
    working = []
    for model_id in CANDIDATES:
        try:
            resp = client.converse(
                modelId=model_id,
                messages=[{"role": "user", "content": [{"text": "Reply with the single word OK."}]}],
                inferenceConfig={"maxTokens": 10},
            )
            text = resp["output"]["message"]["content"][0]["text"].strip()
            print(f"  OK    {model_id}  -> {text!r}")
            working.append(model_id)
        except Exception as exc:  # noqa: BLE001
            msg = str(exc).split(":", 1)[-1].strip()
            print(f"  --    {model_id}  ({type(exc).__name__}: {msg[:90]})")

    print()
    if working:
        print(f'Put this in infra/config.json:\n  "modelId": "{working[0]}"')
        print("then run `cdk deploy` again (from infra/).")
    else:
        print("No Claude model answered. Open Bedrock -> Model catalog -> a Claude model -> Open in playground,")
        print("submit the use-case form, wait ~10 minutes, and run this again.")
        print("Everything except the agent and the summary narrative works without a model (rules mode).")


if __name__ == "__main__":
    main()
