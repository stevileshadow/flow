# Flow - Gestion de flotte et mandats de signalisation

## Overview
Flow is a custom Frappe/ERPNext app for fleet management and signaling mandates. It is designed to be installed as a plugin into a Frappe/ERPNext instance. In this Replit environment, a simple Flask info page is served to present the app metadata.

## Architecture
- **Type**: Frappe/ERPNext custom app (Python)
- **Language**: Python 3.12
- **Web server**: Flask (dev), Gunicorn (production)
- **Port**: 5000

## Key Files
- `app.py` — Flask app serving the info/landing page
- `hooks.py` — Frappe app metadata (app_name, title, publisher, license, etc.)
- `doctype/__init__.py` — Empty Frappe doctype module init

## Running the App
The workflow "Start application" runs `python app.py` on port 5000.

## Deployment
Configured for autoscale deployment using:
```
gunicorn --bind=0.0.0.0:5000 --reuse-port app:app
```

## About the Frappe App
To fully use Flow, install it in a Frappe/ERPNext environment:
```bash
bench get-app flow <repo-url>
bench install-app flow
```
