"""Sends a report e-mail to the operations head and the warehouse manager.

If e-mail is not set up yet (no SMTP_* values in .env), the e-mail is NOT sent: it is saved as a text file in
the 'outbox' folder instead, so you can see exactly what would have been sent.

To really send (free with Gmail): put these in .env  (use a Gmail "App password", never your normal password)
    SMTP_HOST=smtp.gmail.com
    SMTP_PORT=587
    SMTP_USER=you@gmail.com
    SMTP_PASSWORD=your-app-password
    OPS_HEAD_EMAIL=operations.head@example.com
    WH_MANAGER_EMAIL=warehouse.manager@example.com
"""
import os
import smtplib
from datetime import datetime
from email.message import EmailMessage
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")


def send_report(subject: str, body: str) -> str:
    """Returns a short sentence saying what happened."""
    to = [x for x in (os.getenv("OPS_HEAD_EMAIL"), os.getenv("WH_MANAGER_EMAIL")) if x]
    host, user, password = os.getenv("SMTP_HOST"), os.getenv("SMTP_USER"), os.getenv("SMTP_PASSWORD")
    if host and user and password and to:
        msg = EmailMessage()
        msg["Subject"], msg["From"], msg["To"] = subject, user, ", ".join(to)
        msg.set_content(body)
        try:
            with smtplib.SMTP(host, int(os.getenv("SMTP_PORT", "587")), timeout=20) as server:
                server.starttls()
                server.login(user, password)
                server.send_message(msg)
            return f"E-mail sent to {', '.join(to)}"
        except Exception as error:                      # never let a mail problem lose the receipt
            note = f"(e-mail failed: {error}) "
    else:
        note = ""
    outbox = ROOT / "outbox"
    outbox.mkdir(exist_ok=True)
    name = datetime.now().strftime("%Y%m%d-%H%M%S-%f") + ".txt"
    (outbox / name).write_text(f"To: {', '.join(to) or '(not set in .env)'}\nSubject: {subject}\n\n{body}\n", encoding="utf-8")
    return f"{note}E-mail not sent (not set up); saved a copy in outbox/{name}"
