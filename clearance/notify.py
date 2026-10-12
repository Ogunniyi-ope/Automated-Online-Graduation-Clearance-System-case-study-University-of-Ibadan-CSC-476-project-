"""Notification service: in-app message, e-mail (SMTP when configured) and SMS (gateway interface).

In the prototype the SMS channel is *simulated*: the message is stored with delivery='simulated'.
Plug a real provider into `_send_sms` to go live. E-mail is sent through SMTP when SMTP_HOST is set,
otherwise it is stored with delivery='simulated'.
"""
import smtplib
from email.message import EmailMessage

from flask import current_app

from .db import execute, now, query


def _send_email(to_addr: str, subject: str, body: str) -> str:
    cfg = current_app.config
    if not cfg.get("SMTP_HOST"):
        return "simulated"
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = cfg["MAIL_FROM"], to_addr, subject
    msg.set_content(body)
    try:
        with smtplib.SMTP(cfg["SMTP_HOST"], cfg["SMTP_PORT"], timeout=10) as smtp:
            smtp.starttls()
            if cfg.get("SMTP_USER"):
                smtp.login(cfg["SMTP_USER"], cfg["SMTP_PASSWORD"])
            smtp.send_message(msg)
        return "sent"
    except Exception:       # a mail failure must never block a clearance decision
        return "failed"


def _send_sms(phone: str, text: str) -> str:
    return "simulated"      # replace with a call to an SMS gateway


def notify(user_id: int, message: str, subject: str = "Graduation clearance update") -> None:
    user = query("SELECT email, phone FROM users WHERE user_id = ?", (user_id,), one=True)
    if user is None:
        return
    ts = now()
    execute("INSERT INTO notifications (user_id, message, channel, delivery, sent_at) VALUES (?, ?, 'in_app', 'recorded', ?)",
            (user_id, message, ts))
    if user["email"]:
        status = _send_email(user["email"], subject, message)
        execute("INSERT INTO notifications (user_id, message, channel, delivery, sent_at, is_read) VALUES (?, ?, 'email', ?, ?, 1)",
                (user_id, message, status, ts))
    if user["phone"]:
        status = _send_sms(user["phone"], message[:160])
        execute("INSERT INTO notifications (user_id, message, channel, delivery, sent_at, is_read) VALUES (?, ?, 'sms', ?, ?, 1)",
                (user_id, message[:160], status, ts))


def notify_unit(unit_id: int, message: str) -> None:
    for row in query("SELECT user_id FROM users WHERE unit_id = ? AND is_active = 1", (unit_id,)):
        notify(row["user_id"], message, "New clearance request awaiting action")
