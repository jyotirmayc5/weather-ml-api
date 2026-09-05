"""Sends the Step 6 live scorecard report (scripts/step6_live_scorecard.py)
as an email, for a weekly Windows Task Scheduler job -- built after the
cloud routine (/schedule) approach hit a real wall: the claude.ai Gmail
connector shown as "connected" is a chat-side integration, not an MCP
connector cloud routines can attach to, so no email could be sent from
there. This script uses Gmail's SMTP interface directly with an App
Password instead, which doesn't depend on that connector system at all.

Setup required in .env (gitignored, never commit):
  GMAIL_ADDRESS=youraddress@gmail.com       # the account sending the email
  GMAIL_APP_PASSWORD=xxxx xxxx xxxx xxxx    # NOT your normal Gmail password --
                                             # generate one at
                                             # https://myaccount.google.com/apppasswords
                                             # (requires 2-Step Verification enabled)
  SCORECARD_EMAIL_TO=jyotirmayc@gmail.com   # who receives the weekly report

Usage: venv/Scripts/python.exe -m scripts.send_weekly_scorecard_email
"""
import smtplib
import sys
from datetime import date
from email.mime.text import MIMEText

from scripts.step6_live_scorecard import build_report


def load_env_var(name: str, env_path=".env") -> str:
    with open(env_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line.startswith(f"{name}="):
                return line.split("=", 1)[1]
    raise RuntimeError(f"{name} not found in {env_path} -- see this script's docstring for setup.")


def main():
    gmail_address = load_env_var("GMAIL_ADDRESS")
    gmail_app_password = load_env_var("GMAIL_APP_PASSWORD")
    recipient = load_env_var("SCORECARD_EMAIL_TO")

    report = build_report()

    msg = MIMEText(report)
    msg["Subject"] = f"Weekly Step 6 scorecard -- {date.today().isoformat()}"
    msg["From"] = gmail_address
    msg["To"] = recipient

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
        smtp.login(gmail_address, gmail_app_password)
        smtp.send_message(msg)

    print(f"Sent to {recipient}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
