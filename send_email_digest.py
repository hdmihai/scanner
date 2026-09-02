#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
send_email_digest.py
======================
Trimite un email cu rezumatul ultimei scanari, o data la 4 ore (nu la
fiecare rulare orara - scriptul verifica singur ora si iese instant daca
nu e momentul).

De ce nu trimit dashboard-ul HTML complet ca si continut de email?
Clientii de email (Gmail, Outlook, Apple Mail) au suport CSS mult mai
limitat decat un browser - CSS Grid, fonturi Google, <style> in <head>
deseori NU functioneaza. Ca sa nu trimit ceva stricat, emailul contine:
  1. un rezumat simplu, cu stiluri inline (functioneaza peste tot)
  2. link catre pagina live (GitHub Pages) - acolo arata complet, cu grafic
  3. fisierul index.html complet atasat, pentru offline / daca vrei tot

Necesita (gratuit, cont Gmail obisnuit):
  EMAIL_ADDRESS       - adresa de gmail de la care se trimite
  EMAIL_APP_PASSWORD  - "App Password" de 16 caractere, NU parola normala.
                        Se genereaza la myaccount.google.com/apppasswords
                        (necesita 2FA activat pe cont). Gratuit, fara limite
                        relevante pentru volumul asta (cateva emailuri/zi).
  EMAIL_TO            - adresa unde trimiti (poate fi aceeasi cu EMAIL_ADDRESS)
"""

import json
import os
import smtplib
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.application import MIMEApplication

DATA_DIR = "data"
HISTORY_FILE = os.path.join(DATA_DIR, "scan_history.json")
DASHBOARD_FILE = os.path.join("docs", "index.html")

EMAIL_EVERY_N_HOURS = 4
GITHUB_PAGES_URL = os.environ.get("GITHUB_PAGES_URL", "https://hdmihai.github.io/scanner/")

EMAIL_ADDRESS = os.environ.get("EMAIL_ADDRESS", "")
EMAIL_APP_PASSWORD = os.environ.get("EMAIL_APP_PASSWORD", "")
EMAIL_TO = os.environ.get("EMAIL_TO", EMAIL_ADDRESS)
SMTP_HOST = os.environ.get("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "465"))


def load_json(path, default):
    if not os.path.exists(path):
        return default
    with open(path) as f:
        return json.load(f)


def should_send_now():
    return datetime.now(timezone.utc).hour % EMAIL_EVERY_N_HOURS == 0


def fmt_price(v):
    if v is None:
        return "-"
    return f"{v:,.4f}" if v < 100 else f"{v:,.2f}"


def build_summary_html(scan):
    best = scan.get("best_candidate")
    deep = scan.get("deep_analysis") or {}
    plan = deep.get("plan") or {}

    def row(label, value):
        return (f'<tr><td style="padding:4px 10px 4px 0;color:#8A93A0;'
                f'font-family:monospace;font-size:13px;">{label}</td>'
                f'<td style="padding:4px 0;font-family:monospace;font-size:13px;'
                f'font-weight:bold;">{value}</td></tr>')

    if not best:
        plan_block = "<p>Niciun candidat cu semnal clar in scanarea curenta.</p>"
    else:
        color = "#34D399" if best["direction"] == "LONG" else "#FB7A6C"
        plan_block = f'''
        <p style="font-family:monospace;font-size:20px;margin:0 0 10px;">
          {best["symbol"]} &nbsp;
          <span style="color:{color};font-weight:bold;">{best["direction"]}</span>
        </p>
        <table cellpadding="0" cellspacing="0">
          {row("CONFIDENCE", f'{best["probability"]}%')}
          {row("ENTRY", fmt_price(plan.get("entry")))}
          {row("SL", fmt_price(plan.get("sl")))}
          {row("TP1", fmt_price(plan.get("tp1")))}
          {row("TP2", fmt_price(plan.get("tp2")))}
          {row("EXPECTED R", f'{plan.get("expected_r", "-")}R')}
        </table>
        '''

    def mini_list(rows):
        if not rows:
            return '<p style="color:#8A93A0;font-size:13px;">(niciun semnal)</p>'
        items = "".join(
            f'<li style="font-family:monospace;font-size:13px;margin-bottom:3px;">'
            f'{r["symbol"]} &middot; {r["risk_adjusted"]}/100 &middot; {r["probability"]}%</li>'
            for r in rows
        )
        return f'<ul style="margin:0;padding-left:18px;">{items}</ul>'

    return f'''
    <div style="font-family:-apple-system,Arial,sans-serif;background:#0B0F14;
                color:#E7E4DD;padding:24px;max-width:600px;margin:0 auto;">
      <p style="font-family:monospace;letter-spacing:2px;color:#E6B450;
                font-size:12px;text-transform:uppercase;margin:0 0 4px;">SCANLINE</p>
      <p style="color:#8A93A0;font-size:12px;margin:0 0 20px;">
        Scan {scan.get("scan_time", "-")} &middot; universe {scan.get("universe_size", 0)}
      </p>

      <div style="background:#131920;border-radius:8px;padding:16px;margin-bottom:16px;">
        {plan_block}
      </div>

      <table width="100%" cellpadding="0" cellspacing="0"><tr>
        <td width="50%" valign="top" style="padding-right:8px;">
          <p style="color:#34D399;font-size:12px;text-transform:uppercase;margin:0 0 6px;">Top long</p>
          {mini_list(scan.get("top_long", []))}
        </td>
        <td width="50%" valign="top" style="padding-left:8px;">
          <p style="color:#FB7A6C;font-size:12px;text-transform:uppercase;margin:0 0 6px;">Top short</p>
          {mini_list(scan.get("top_short", []))}
        </td>
      </tr></table>

      <p style="margin-top:22px;">
        <a href="{GITHUB_PAGES_URL}" style="background:#E6B450;color:#0B0F14;
           text-decoration:none;padding:10px 18px;border-radius:6px;
           font-family:-apple-system,Arial,sans-serif;font-size:14px;font-weight:bold;
           display:inline-block;">Deschide dashboard-ul complet &rarr;</a>
      </p>
      <p style="color:#8A93A0;font-size:11px;margin-top:20px;line-height:1.5;">
        Generat automat. Scorurile si planul AI sunt euristici proprii, nu
        recomandari financiare.
      </p>
    </div>
    '''


def send_email(subject, html_body, attachment_path=None):
    if not (EMAIL_ADDRESS and EMAIL_APP_PASSWORD and EMAIL_TO):
        print("[!] EMAIL_ADDRESS / EMAIL_APP_PASSWORD / EMAIL_TO nu sunt toate setate - sar peste email.")
        return

    msg = MIMEMultipart("mixed")
    msg["Subject"] = subject
    msg["From"] = EMAIL_ADDRESS
    msg["To"] = EMAIL_TO
    msg.attach(MIMEText(html_body, "html"))

    if attachment_path and os.path.exists(attachment_path):
        with open(attachment_path, "rb") as f:
            part = MIMEApplication(f.read(), Name="dashboard.html")
        part["Content-Disposition"] = 'attachment; filename="dashboard.html"'
        msg.attach(part)

    try:
        with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT) as server:
            server.login(EMAIL_ADDRESS, EMAIL_APP_PASSWORD)
            server.sendmail(EMAIL_ADDRESS, EMAIL_TO, msg.as_string())
        print(f"Email trimis catre {EMAIL_TO}.")
    except Exception as e:
        print(f"[!] Trimitere email esuata: {e}")


def main():
    if not should_send_now():
        print(f"Nu e momentul (trimit doar la fiecare {EMAIL_EVERY_N_HOURS}h) - sar peste.")
        return

    history = load_json(HISTORY_FILE, [])
    if not history:
        print("Nu exista inca nicio scanare.")
        return

    scan = history[-1]
    best = scan.get("best_candidate")
    subject = (f"SCANLINE · {best['symbol']} {best['direction']} · {scan['scan_time']}"
               if best else f"SCANLINE · fara semnal · {scan['scan_time']}")

    html_body = build_summary_html(scan)
    send_email(subject, html_body, attachment_path=DASHBOARD_FILE)


if __name__ == "__main__":
    main()
