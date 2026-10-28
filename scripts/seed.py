#!/usr/bin/env python3
"""Load the monitored person and their family contacts into DynamoDB.

Usage:
    python scripts/seed.py seed/family.json            # reads table names from the CareCircle stack outputs
    python scripts/seed.py seed/family.json --region ap-southeast-2
"""

import argparse
import json
import os
import sys

import boto3

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))


def stack_outputs(region: str) -> dict:
    cfn = boto3.client("cloudformation", region_name=region)
    outputs = cfn.describe_stacks(StackName="CareCircle")["Stacks"][0].get("Outputs", [])
    return {o["OutputKey"]: o["OutputValue"] for o in outputs}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("file")
    cfg_file = os.path.join(os.path.dirname(__file__), "..", "infra", "config.json")
    default_region = (json.load(open(cfg_file, encoding="utf-8")).get("region") if os.path.exists(cfg_file)
                      else None) or os.environ.get("AWS_REGION", "ap-southeast-2")
    ap.add_argument("--region", default=default_region)
    ap.add_argument("--skip-ses", action="store_true", help="don't create SES identities for contact emails")
    args = ap.parse_args()

    with open(args.file, encoding="utf-8") as fh:
        data = json.load(fh)

    out = stack_outputs(args.region)
    os.environ.update({
        "AWS_REGION": args.region, "CC_SKIP_SSM": "1",
        "PATIENTS_TABLE": out["PatientsTable"], "CONTACTS_TABLE": out["ContactsTable"],
    })
    from carecircle import store  # noqa: E402 - needs env first

    patient = data["patient"]
    store.put_patient_keep_chat(patient)
    print(f"patient  {patient['patientId']}: {patient['name']}")
    emails = []
    for c in data["contacts"]:
        if "X" in c["phone"].upper() or "Y" in c["phone"].upper():
            sys.exit(f"Replace the placeholder phone number for {c['name']} in {args.file}")
        if any(x in c.get("email", "") for x in ("example.com", "you@gmail.com", "second-person@")):
            sys.exit(f"Replace the example email for {c['name']} in {args.file}")
        c["patientId"] = patient["patientId"]
        store.put_contact(c)
        if c.get("email"):
            emails.append(c["email"])
        print(f"contact  {c['name']} ({c['relation']}) priority {c['priority']} "
              f"id {store.normalize_phone(c['phone'])} email {c.get('email') or '-'}")

    cfg_path = os.path.join(os.path.dirname(__file__), "..", "infra", "config.json")
    if os.path.exists(cfg_path):
        with open(cfg_path, encoding="utf-8") as fh:
            sender = json.load(fh).get("sesFromEmail")
        if sender:
            emails.append(sender)
    if not args.skip_ses:
        verify_emails(args.region, emails)
    print("done.")


def verify_emails(region: str, emails: list):
    """While SES is in sandbox mode it only delivers to verified addresses.
    Creating the identity makes AWS send each person a verification link."""
    ses = boto3.client("sesv2", region_name=region)
    for email in sorted(set(emails)):
        try:
            status = ses.get_email_identity(EmailIdentity=email).get("VerificationStatus")
        except ses.exceptions.NotFoundException:
            ses.create_email_identity(EmailIdentity=email)
            status = "PENDING (verification email sent)"
        print(f"ses      {email}: {status}")
    print("         -> each PENDING address must click the link in the 'Amazon Web Services - "
          "Email Address Verification Request' email (check spam).")


if __name__ == "__main__":
    main()
