#!/usr/bin/env bash
# After `agentcore launch`:
#   1. attaches the CareCircle tools policy to the AgentCore execution role
#   2. stores the agent runtime ARN where the dispatch Lambda looks for it
#
# Usage: scripts/connect_agent.sh <agent-runtime-arn> [region]
set -euo pipefail
ARN="${1:?usage: connect_agent.sh <agent-runtime-arn> [region]}"
REGION="${2:-${AWS_REGION:-ap-southeast-2}}"

POLICY_ARN=$(aws cloudformation describe-stacks --stack-name CareCircle --region "$REGION" \
  --query "Stacks[0].Outputs[?OutputKey=='AgentToolsPolicyArn'].OutputValue" --output text)

RUNTIME_ID="${ARN##*/}"
ROLE_ARN=$(aws bedrock-agentcore-control get-agent-runtime --agent-runtime-id "$RUNTIME_ID" \
  --region "$REGION" --query roleArn --output text)
ROLE_NAME="${ROLE_ARN##*/}"

echo "Attaching $POLICY_ARN to role $ROLE_NAME"
aws iam attach-role-policy --role-name "$ROLE_NAME" --policy-arn "$POLICY_ARN"

echo "Saving agent ARN to SSM /carecircle/agent-runtime-arn"
aws ssm put-parameter --name /carecircle/agent-runtime-arn --type String --value "$ARN" \
  --overwrite --region "$REGION" >/dev/null
echo "Agent connected. The dashboard badge should now read 'AgentCore agent online'."
