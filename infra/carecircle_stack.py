"""CareCircle infrastructure (AWS CDK v2, Python)."""

import os

from aws_cdk import (
    CfnOutput, Duration, RemovalPolicy, Stack,
    aws_apigatewayv2 as apigw,
    aws_apigatewayv2_integrations as integrations,
    aws_dynamodb as ddb,
    aws_events as events,
    aws_events_targets as targets,
    aws_iam as iam,
    aws_lambda as lambda_,
    aws_lambda_event_sources as sources,
    aws_logs as logs,
    aws_sns as sns,
    aws_ssm as ssm,
)
from constructs import Construct

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PARAM = "/carecircle/config"
AGENT_ARN_PARAM = "/carecircle/agent-runtime-arn"


class CareCircleStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, cfg: dict, **kwargs):
        super().__init__(scope, construct_id, **kwargs)

        # ------------------------------------------------------------ data
        def table(name, pk, pk_type=ddb.AttributeType.STRING, sk=None, ttl=None):
            return ddb.Table(
                self, name,
                partition_key=ddb.Attribute(name=pk, type=pk_type),
                sort_key=ddb.Attribute(name=sk, type=ddb.AttributeType.NUMBER) if sk else None,
                billing_mode=ddb.BillingMode.PAY_PER_REQUEST,
                time_to_live_attribute=ttl,
                removal_policy=RemovalPolicy.DESTROY,
            )

        vitals = table("Vitals", "patientId", sk="ts", ttl="expiresAt")
        patients = table("Patients", "patientId")
        contacts = table("Contacts", "phone")
        contacts.add_global_secondary_index(
            index_name="byPatient",
            partition_key=ddb.Attribute(name="patientId", type=ddb.AttributeType.STRING))
        incidents = table("Incidents", "incidentId")
        incidents.add_global_secondary_index(
            index_name="byPatient",
            partition_key=ddb.Attribute(name="patientId", type=ddb.AttributeType.STRING),
            sort_key=ddb.Attribute(name="createdAt", type=ddb.AttributeType.NUMBER))
        all_tables = [vitals, patients, contacts, incidents]

        # ------------------------------------------------- HTTP API (routes below)
        http_api = apigw.HttpApi(
            self, "HttpApi", api_name="carecircle",
            cors_preflight=apigw.CorsPreflightOptions(
                allow_origins=["*"], allow_headers=["content-type", "x-api-token"],
                allow_methods=[apigw.CorsHttpMethod.GET, apigw.CorsHttpMethod.POST]))

        # ---------------------------------------------- runtime configuration
        runtime_cfg = {
            "PUBLIC_BASE_URL": http_api.api_endpoint,
            "ACTION_SECRET": cfg["actionSecret"],
            "VITALS_TABLE": vitals.table_name,
            "PATIENTS_TABLE": patients.table_name,
            "CONTACTS_TABLE": contacts.table_name,
            "INCIDENTS_TABLE": incidents.table_name,
            "WA_PHONE_NUMBER_ID": cfg.get("waPhoneNumberId", ""),
            "WA_META_API_VERSION": cfg.get("waMetaApiVersion", "v20.0"),
            "WA_TEMPLATE_ALERT": cfg.get("waTemplateName", "care_alert"),
            "WA_TEMPLATE_LANG": cfg.get("waTemplateLanguage", "en"),
            "SMS_ORIGINATION_IDENTITY": cfg.get("smsOriginationIdentity", ""),
            "SES_FROM_EMAIL": cfg.get("sesFromEmail", ""),
            "MODEL_ID": cfg.get("modelId", "au.anthropic.claude-sonnet-4-5-20250929-v1:0"),
            "ACK_TIMEOUT_MIN": str(cfg.get("ackTimeoutMinutes", 5)),
            "OFFLINE_MINUTES": str(cfg.get("offlineMinutes", 0)),
            "EMERGENCY_NUMBER": str(cfg.get("emergencyNumber", "108")),
            "DRY_RUN": str(cfg.get("dryRun", False)).lower(),
            "AGENT_ARN_PARAM": AGENT_ARN_PARAM,
        }
        config_param = ssm.StringParameter(
            self, "RuntimeConfig", parameter_name=CONFIG_PARAM,
            string_value=self.to_json_string(runtime_cfg),
            description="CareCircle runtime config (read by the AgentCore agent)")

        # ------------------------------------------- shared permissions policy
        tools_policy = iam.ManagedPolicy(
            self, "AgentToolsPolicy",
            description="Permissions the CareCircle agent tools need (attach to the AgentCore execution role)",
            statements=[
                iam.PolicyStatement(
                    actions=["dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:UpdateItem",
                             "dynamodb:Query", "dynamodb:Scan", "dynamodb:BatchWriteItem"],
                    resources=[t.table_arn for t in all_tables] + [f"{t.table_arn}/index/*" for t in all_tables]),
                iam.PolicyStatement(actions=["social-messaging:SendWhatsAppMessage"], resources=["*"]),
                iam.PolicyStatement(actions=["sms-voice:SendTextMessage"], resources=["*"]),
                iam.PolicyStatement(actions=["ses:SendEmail", "ses:SendRawEmail"], resources=["*"]),
                iam.PolicyStatement(
                    actions=["ssm:GetParameter"],
                    resources=[self.format_arn(service="ssm", resource="parameter",
                                               resource_name="carecircle/*")]),
                iam.PolicyStatement(
                    actions=["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream",
                             "bedrock:Converse", "bedrock:ConverseStream"],
                    resources=["*"]),
            ])

        # --------------------------------------------------------- lambdas
        boto_layer = lambda_.LayerVersion(
            self, "Boto3Layer",
            code=lambda_.Code.from_asset(os.path.join(ROOT, "build", "boto3-layer")),
            compatible_runtimes=[lambda_.Runtime.PYTHON_3_12],
            description="Recent boto3 with socialmessaging / bedrock-agentcore clients")

        code = lambda_.Code.from_asset(
            os.path.join(ROOT, "backend"),
            exclude=["**/__pycache__", ".bedrock_agentcore*", "**/.venv", "*.pyc", "Dockerfile",
                     ".dockerignore"])

        env = {**runtime_cfg, "API_TOKEN": cfg["apiToken"], "CC_SKIP_SSM": "1"}

        def fn(name, handler, timeout=30, memory=256):
            f = lambda_.Function(
                self, name,
                runtime=lambda_.Runtime.PYTHON_3_12,
                architecture=lambda_.Architecture.ARM_64,
                handler=handler, code=code, layers=[boto_layer],
                timeout=Duration.seconds(timeout), memory_size=memory,
                environment=env, log_retention=logs.RetentionDays.ONE_WEEK)
            f.role.add_managed_policy(tools_policy)
            return f

        dispatch_fn = fn("Dispatch", "handlers.dispatch.handler", timeout=180, memory=512)
        dispatch_fn.add_to_role_policy(iam.PolicyStatement(
            actions=["bedrock-agentcore:InvokeAgentRuntime"], resources=["*"]))

        api_fn = fn("Api", "handlers.api.handler", timeout=29, memory=512)
        inbound_fn = fn("WhatsAppInbound", "handlers.inbound.handler", timeout=30)
        monitor_fn = fn("Monitor", "handlers.monitor.handler", timeout=60)
        summary_fn = fn("DailySummary", "handlers.summary.handler", timeout=120)

        for f in (api_fn, inbound_fn, monitor_fn):
            f.add_environment("DISPATCH_FUNCTION", dispatch_fn.function_name)
            dispatch_fn.grant_invoke(f)
        # The dashboard reads whether the agent ARN is set; dispatch reads the ARN itself.
        for f in (api_fn, dispatch_fn):
            f.add_to_role_policy(iam.PolicyStatement(
                actions=["ssm:GetParameter"],
                resources=[self.format_arn(service="ssm", resource="parameter",
                                           resource_name=AGENT_ARN_PARAM.lstrip("/"))]))

        # ---------------------------------------- WhatsApp inbound (SNS topic)
        wa_topic = sns.Topic(self, "WhatsAppEvents", display_name="CareCircle WhatsApp events")
        wa_topic.add_to_resource_policy(iam.PolicyStatement(
            principals=[iam.ServicePrincipal("social-messaging.amazonaws.com")],
            actions=["sns:Publish"], resources=[wa_topic.topic_arn],
            conditions={"StringEquals": {"aws:SourceAccount": self.account}}))
        inbound_fn.add_event_source(sources.SnsEventSource(wa_topic))

        # ------------------------------------------------------------- API
        integ = integrations.HttpLambdaIntegration("ApiInteg", api_fn)
        for path, method in [("/", apigw.HttpMethod.GET), ("/dashboard", apigw.HttpMethod.GET),
                             ("/status", apigw.HttpMethod.GET), ("/vitals", apigw.HttpMethod.POST),
                             ("/simulate", apigw.HttpMethod.POST), ("/summary", apigw.HttpMethod.POST),
                             ("/ask", apigw.HttpMethod.POST), ("/act", apigw.HttpMethod.GET),
                             ("/act", apigw.HttpMethod.POST),
                             ("/incidents/{id}/resolve", apigw.HttpMethod.POST)]:
            http_api.add_routes(path=path, methods=[method], integration=integ)

        # -------------------------------------------------------- schedules
        events.Rule(self, "MonitorEveryMinute", schedule=events.Schedule.rate(Duration.minutes(1)),
                    targets=[targets.LambdaFunction(monitor_fn)])
        hour, minute = cfg.get("summaryTimeUtc", "14:30").split(":")
        events.Rule(self, "DailySummarySchedule",
                    schedule=events.Schedule.cron(hour=hour, minute=minute),
                    targets=[targets.LambdaFunction(summary_fn)])

        # ---------------------------------------------------------- outputs
        CfnOutput(self, "DashboardUrl", value=f"{http_api.api_endpoint}/?patient=appa")
        CfnOutput(self, "ApiEndpoint", value=http_api.api_endpoint)
        CfnOutput(self, "WhatsAppEventTopicArn", value=wa_topic.topic_arn,
                  description="Set as the event destination of your WABA in AWS End User Messaging Social")
        CfnOutput(self, "AgentToolsPolicyArn", value=tools_policy.managed_policy_arn,
                  description="Attach to the AgentCore execution role created by `agentcore configure`")
        CfnOutput(self, "AgentArnParameter", value=AGENT_ARN_PARAM)
        CfnOutput(self, "ConfigParameter", value=config_param.parameter_name)
        CfnOutput(self, "PatientsTable", value=patients.table_name)
        CfnOutput(self, "ContactsTable", value=contacts.table_name)
