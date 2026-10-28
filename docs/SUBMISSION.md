# Devpost submission kit

Copy-paste material for the AWS CDS Agentic AI Partner Hackathon. Replace anything in `<angle brackets>`.

---

## Project name

**CareCircle** — an AI care coordinator that turns health-band alerts into family action

## Tagline

When Appa's heart races at rest, the right family member knows in seconds, can reply with one tap, and can ask the agent what's happening.

## Text description

### Inspiration

Millions of older adults live on their own while their children work in another city or country.
Many already wear a fitness band that measures heart rate, blood oxygen and falls — but that data
stays inside an app nobody checks. Existing alerting is blunt: either nothing, or the same message
sent to everyone, including false alarms every time Appa goes for his morning walk. Nobody knows
who is handling it, and if nobody answers, nothing else happens.

### What it does

1. A health band (or our simulator) uploads readings every minute.
2. Explainable rules flag a possible concern: sustained high heart rate **while at rest**, low SpO2,
   very low heart rate, or a fall. High heart rate during exercise is ignored.
3. An **Amazon Bedrock AgentCore** agent reads the incident, the person's profile (e.g. "usual resting
   HR 65–75, walks 6–7 am") and the last hour of vitals, then decides severity and a plan:
   - *warning* → alert the priority-1 contact, escalate in 5 minutes
   - *critical* (fall, very high HR, SpO2 < 88%) → alert everyone with the emergency number, escalate in 2 minutes
   - *false alarm* → record why and message nobody
4. Alerts go out by **Amazon SES** email with one-tap reply buttons — **I'm checking**, **Show trend**,
   **All fine now** — HMAC-signed so no login is needed. Family members can also ask questions in the
   dashboard ("How is Appa now?").
5. The agent acts on replies: acknowledging stops escalation and tells the other relatives who is
   handling it; questions are answered with real numbers from the band; "all fine" closes the incident.
6. If nobody acknowledges, the agent re-checks the vitals and escalates to the next contact
   (and by **SMS** through AWS End User Messaging SMS when configured).
7. Every evening each family member gets a **daily summary email** with a short narrative written by
   Amazon Bedrock.
8. A live dashboard shows vitals and the full timeline of every agent decision and message.

### How we built it

- **Amazon SES** for alert emails (with signed action links), conversational follow-ups and daily summaries.
- **AWS End User Messaging SMS** as an escalation channel, and **AWS End User Messaging Social
  (WhatsApp)** built in as an optional two-way channel (template alerts, 24-hour window tracking,
  inbound replies via SNS).
- **Amazon Bedrock AgentCore Runtime** hosts a **Strands Agents** agent with 10 tools (vitals, incidents,
  contacts, send alert, send message, SMS, email, update incident, schedule escalation) using Claude on
  **Amazon Bedrock**. The agent is stateless; all state lives in DynamoDB, so invocations are safe to retry.
- Channel routing picks the right channel per family member and replies where they last talked to us.
- **AWS Lambda**, **API Gateway HTTP API**, **DynamoDB** (vitals with TTL, incidents with an append-only
  timeline), **EventBridge** (1-minute escalation monitor, daily schedule), **SSM Parameter Store**,
  all deployed with **AWS CDK**.
- Safety-critical state changes (acknowledge, resolve) are applied deterministically the moment a
  button is tapped; the agent then coordinates. A **deterministic fallback** handles every event if
  the agent is unavailable, so an alert is never lost.
- 26 automated tests cover detection, signed-link security, and the alert → reply → acknowledge,
  escalation, coordination and Q&A flows.

### Impact

- **Time for the family to know**: from "whenever someone opens the band app" to **under a minute**.
- **Fewer false alarms**: activity context (steps/min) and sustained-duration checks suppress the most
  common false alarm, high heart rate during exercise; the agent can also mark false alarms.
- **No single point of failure**: automatic escalation through the family, across channels, with a
  deterministic fallback if the AI is down.
- **Repeatable offering** for elder-care agencies, assisted-living operators, home-nursing providers and
  insurers' remote-monitoring programs; any wearable plugs in through the `/vitals` API.

### Challenges

- Avoiding alert fatigue: deduplicating ongoing incidents, ignoring exercise, letting the agent close false alarms.
- Making replies frictionless without accounts: signed one-tap links, with a confirm page so email
  link-scanners can't acknowledge an alert by accident.
- Making AI decisions auditable: every tool call writes to the incident timeline.

### What's next

- Real wearable connectors (Android Health Connect, Fitbit, Apple HealthKit via a companion app)
- WhatsApp and RCS as primary channels in production, with rich "Call Appa" / "Call doctor" actions
- AgentCore Memory for long-term per-person baselines
- Voice-call escalation and a caregiver rota

### Built with

amazon-ses, aws-end-user-messaging-sms, aws-end-user-messaging-social, amazon-bedrock-agentcore,
amazon-bedrock, strands-agents, claude, aws-lambda, amazon-dynamodb, amazon-api-gateway,
amazon-eventbridge, amazon-sns, aws-cdk, python

---

## ~3-minute demo video script

Setup: dashboard on the left of the screen, the priority-1 inbox (phone mirrored or a second browser
window) on the right, second inbox in another tab. Set `"ackTimeoutMinutes": 2` in `infra/config.json`
and `cdk deploy` before recording. Close old incidents first.

| Time | Screen | Say |
|---|---|---|
| 0:00–0:20 | Architecture diagram | "Appa is 72 and lives alone. His band measures heart rate, oxygen and falls — but nobody watches it. CareCircle turns that data into family action, coordinated by an agent on Amazon Bedrock AgentCore, delivered through AWS Communication Developer Services." |
| 0:20–0:35 | Dashboard, press **Morning walk** | "First the false alarm every band gets wrong: heart rate 135 — but he's walking 110 steps a minute. No alert." |
| 0:35–1:05 | Press **Racing heart**. Timeline: agent assessment. Inbox: alert email arrives | "Now 130 bpm while resting. The agent reads his profile and last hour of data, rates it a warning, and emails me — the priority-1 contact — through Amazon SES. It has already scheduled an escalation." |
| 1:05–1:25 | In the email tap **Show trend** | "One tap, no login: here's the last hour, straight from the band." |
| 1:25–1:50 | Dashboard chat: *Is it getting better or worse?* → agent answers | "I can also just ask. The agent answers from the real readings, not a canned message." |
| 1:50–2:05 | Tap **I'm checking**. Timeline: *acknowledged · escalation stopped* | "When I acknowledge, escalation stops." |
| 2:05–2:40 | Close it, press **Fall**. Both inboxes get the critical alert with the emergency number. Second inbox taps **I'm checking** → first inbox gets "Priya is checking on Appa" | "A fall is critical: everyone is alerted, and the agent coordinates who's going, so nobody's left guessing. If nobody responds, it escalates to the next person." |
| 2:40–2:52 | Press **Email today's summary**, open the email | "Every evening the family gets a summary written by Bedrock." |
| 2:52–3:00 | Architecture | "Amazon SES and End User Messaging for delivery, AgentCore for decisions. CareCircle." |

Optional: show an older incident whose timeline has the automatic escalation (let one alert sit for
2 minutes before recording).

---

## Submission checklist

- [ ] Repository on GitHub (public with the MIT license visible in *About*, or private and shared with
      **testing@devpost.com** and **aws-cds-partner@amazon.com**)
- [ ] `infra/config.json` and `seed/family.json` **not** committed (they're in `.gitignore`)
- [ ] Architecture diagram (`docs/architecture.png`)
- [ ] Text description (above)
- [ ] ~3-minute video (YouTube / Vimeo, unlisted is fine)
- [ ] Deployed URL: the `DashboardUrl` output, plus the API token in the submission notes for judges
- [ ] **ACE opportunity ID** in AWS Partner Central with campaign code **"AWS CDS Agentic AI Hackathon -Sept. 2026"**
