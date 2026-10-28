# WhatsApp message templates (optional channel)

Only needed if you enable WhatsApp (`waPhoneNumberId` in `infra/config.json`). The demo works with email alone.

WhatsApp only lets a business start a conversation with an **approved template**. Once a family
member replies, a 24-hour customer-service window opens and the CareCircle agent can chat freely.
CareCircle tracks that window per contact (`lastInboundAt`) and picks the right message type
automatically (`carecircle/actions.py → _whatsapp`).

**Submit these as early as possible** — approval can take minutes or up to a day.
Create them in **Meta WhatsApp Manager → Message templates** for the WABA linked to AWS End User
Messaging Social.

## `care_alert` — English (`en`)

| Field | Value |
|---|---|
| Category | **Utility** |
| Name | `care_alert` |
| Language | English (`en`) |
| Header | *(none)* |
| Body | `CareCircle health alert for {{1}}: {{2}}. Latest reading: {{3}}. Please check on them. Reply below or send any question about their readings.` |
| Footer | `Monitoring aid, not a medical device` |
| Buttons | Quick reply: `I'm checking` · Quick reply: `Show trend` |

Sample values (Meta asks for these during review):

- `{{1}}` → `Appa`
- `{{2}}` → `heart rate has stayed around 130 bpm for 5 minutes while resting`
- `{{3}}` → `HR 131 bpm, SpO2 97%`

## `care_alert` — Tamil (`ta`) *(optional, great for the demo)*

Add a second language to the **same** template name.

| Field | Value |
|---|---|
| Body | `CareCircle உடல்நல எச்சரிக்கை - {{1}}: {{2}}. சமீபத்திய அளவீடு: {{3}}. தயவுசெய்து அவரைச் சரிபார்க்கவும். கீழே பதிலளிக்கவும் அல்லது அவரது அளவீடுகள் பற்றி கேள்வி கேட்கவும்.` |
| Footer | `இது மருத்துவ சாதனம் அல்ல` |
| Buttons | Quick reply: `நான் பார்க்கிறேன்` · Quick reply: `போக்கைக் காட்டு` |

Set `"language": "ta"` on a contact in `seed/family.json` to receive the Tamil version. The agent
writes the alert text in that language too.

## How replies come back

Quick-reply taps arrive as `type: "button"` messages; typed replies as `type: "text"`. Both are
parsed in `carecircle/whatsapp_events.py` and handed to the agent as a `family_message` event.
Read receipts (`statuses[].status == "read"`) are written to the incident timeline as
"read the alert".

## Notes

- Template parameters cannot contain new lines, tabs or more than four spaces in a row;
  `messaging._template_param` cleans them.
- Change the template name/language in `infra/config.json` (`waTemplateName`, `waTemplateLanguage`).
