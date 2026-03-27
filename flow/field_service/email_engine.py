"""
flow.field_service.email_engine
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Moteur d'email intelligent pour le module Field Service.

Deux modes d'utilisation :
  - Automatique  : appelé depuis on_update / tasks.py selon les transitions
                   de statut et les règles métier (fenêtre horaire, anti-doublon)
  - Manuel       : get_composer_payload() retourne un dict JSON pour pré-remplir
                   le CommunicationComposer Frappe depuis le JS

Architecture :
  Événement → config (template, destinataires, PDF, cooldown, horaire)
           → should_send() (anti-spam + fenêtre horaire)
           → _resolve_recipients() (client / technicien / manager)
           → _render_template() (Jinja via frappe.render_template)
           → frappe.sendmail() avec Communication automatique
"""
import frappe
from frappe import _
from frappe.utils import add_to_date, now_datetime


# ------------------------------------------------------------------ #
#  Catalogue des événements email                                      #
# ------------------------------------------------------------------ #
#
# recipients : "customer" | "technician" | "manager"
# cooldown_h : heures minimales entre deux envois du même event sur le même doc
# night_block : si True, ne pas envoyer entre NIGHT_START et NIGHT_END
# attach_pdf  : joindre le PDF d'impression du FSO
# statuses    : si présent, event disponible seulement pour ces statuts (manuel)
# auto        : si False, event accessible uniquement en mode manuel
#
EMAIL_EVENTS: dict[str, dict] = {
    "creation": {
        "label": "Confirmation de création",
        "icon": "✅",
        "template": "fsm-confirmation-creation",
        "recipients": "customer",
        "auto": True,
        "attach_pdf": False,
        "cooldown_h": 24,
        "night_block": False,
    },
    "assignation": {
        "label": "Assignation au technicien",
        "icon": "👷",
        "template": "fsm-rappel-intervention-technicien",
        "recipients": "technician",
        "auto": True,
        "attach_pdf": False,
        "cooldown_h": 1,
        "night_block": False,
    },
    "pre_visite": {
        "label": "Pré-visite client (J-1)",
        "icon": "📅",
        "template": "fsm-notification-pre-visite-client",
        "recipients": "customer",
        "auto": True,
        "attach_pdf": False,
        "cooldown_h": 12,
        "night_block": False,
    },
    "demarrage": {
        "label": "Démarrage de l'intervention",
        "icon": "🔧",
        "template": "fsm-demarrage-intervention",
        "recipients": "customer",
        "auto": True,
        "attach_pdf": False,
        "cooldown_h": 4,
        "night_block": True,
        "statuses": ["En cours"],
    },
    "cloture": {
        "label": "Rapport de clôture (avec PDF)",
        "icon": "📋",
        "template": "fsm-rapport-cloture",
        "recipients": "customer",
        "auto": True,
        "attach_pdf": True,
        "cooldown_h": 0,
        "night_block": False,
        "statuses": ["Terminé"],
    },
    "attente_pieces": {
        "label": "Attente de pièces",
        "icon": "⏳",
        "template": "fsm-attente-pieces",
        "recipients": "customer",
        "auto": True,
        "attach_pdf": False,
        "cooldown_h": 8,
        "night_block": True,
        "statuses": ["En attente de pièces"],
    },
    "facture": {
        "label": "Envoi de facture (avec PDF)",
        "icon": "💶",
        "template": "fsm-envoi-facture",
        "recipients": "customer",
        "auto": False,
        "attach_pdf": True,
        "cooldown_h": 0,
        "night_block": True,
        "statuses": ["Facturé"],
    },
    "message_libre": {
        "label": "Message libre",
        "icon": "✉️",
        "template": None,
        "recipients": "customer",
        "auto": False,
        "attach_pdf": False,
        "cooldown_h": 0,
        "night_block": False,
    },
}

# Transition de statut → event email automatique
STATUS_TO_EVENT: dict[str, str] = {
    "En cours":              "demarrage",
    "Terminé":               "cloture",
    "En attente de pièces":  "attente_pieces",
    "Facturé":               "facture",
}

NIGHT_START = 21   # 21 h00
NIGHT_END   = 7    #  7 h00


# ------------------------------------------------------------------ #
#  Helpers internes                                                    #
# ------------------------------------------------------------------ #

def _is_night() -> bool:
    h = now_datetime().hour
    return h >= NIGHT_START or h < NIGHT_END


def _resolve_recipients(fso, recipient_key: str) -> list[str]:
    """Résout la liste d'adresses email selon la clé de destinataire."""
    emails: list[str] = []
    if recipient_key == "customer":
        if fso.contact_email:
            emails.append(fso.contact_email)
        elif fso.contact_person:
            em = frappe.db.get_value("Contact", fso.contact_person, "email_id")
            if em:
                emails.append(em)
    elif recipient_key == "technician":
        if fso.assigned_to:
            uid = frappe.db.get_value("Employee", fso.assigned_to, "user_id")
            if uid:
                emails.append(uid)
        if not emails and getattr(fso, "technicians", None):
            for t in fso.technicians:
                if getattr(t, "user_id", None):
                    emails.append(t.user_id)
                    break  # technicien principal seulement
    elif recipient_key == "manager":
        mgrs = frappe.db.get_all(
            "Has Role",
            filters={"role": "Field Service Manager", "parenttype": "User"},
            fields=["parent"],
        )
        for m in mgrs:
            em = frappe.db.get_value("User", m.parent, "email")
            if em:
                emails.append(em)
    return list(dict.fromkeys(emails))  # dédoublonnage en conservant l'ordre


def _already_sent(fso_name: str, event_type: str, cooldown_h: float) -> bool:
    """Vérifie si un email de ce type a déjà été envoyé récemment."""
    if cooldown_h <= 0:
        return False
    since = add_to_date(now_datetime(), hours=-cooldown_h)
    count = frappe.db.count(
        "Communication",
        {
            "reference_doctype": "Field Service Order",
            "reference_name": fso_name,
            "subject": ["like", f"%[FSM-{event_type}]%"],
            "creation": [">", since],
        },
    )
    return count > 0


def _render_template(template_name: str, fso) -> dict[str, str]:
    """Rend un Email Template avec le contexte du FSO."""
    try:
        tpl = frappe.get_doc("Email Template", template_name)
        ctx = {"doc": fso, "frappe": frappe}
        subject = frappe.render_template(tpl.subject, ctx)
        message = frappe.render_template(tpl.response, ctx)
        return {"subject": subject, "message": message}
    except Exception:
        frappe.log_error(frappe.get_traceback(), f"FSM Email: render template '{template_name}'")
        name = fso.name if hasattr(fso, "name") else str(fso)
        return {
            "subject": f"Intervention {name}",
            "message": f"<p>Référence intervention : {name}</p>",
        }


def _build_pdf_attachment(fso_name: str) -> list[dict]:
    """Génère le PDF d'impression du FSO comme pièce jointe."""
    try:
        content = frappe.get_print(
            doctype="Field Service Order",
            name=fso_name,
            as_pdf=True,
        )
        return [{"fname": f"intervention-{fso_name}.pdf", "fcontent": content}]
    except Exception:
        frappe.log_error(frappe.get_traceback(), f"FSM Email: PDF — {fso_name}")
        return []


def _get_recent_communications(fso_name: str) -> list[dict]:
    return frappe.db.get_all(
        "Communication",
        filters={
            "reference_doctype": "Field Service Order",
            "reference_name": fso_name,
            "communication_type": "Communication",
        },
        fields=["name", "subject", "creation", "sent_or_received"],
        order_by="creation desc",
        limit=6,
    )


def _event_available(status: str, event_key: str) -> bool:
    cfg = EMAIL_EVENTS.get(event_key, {})
    allowed = cfg.get("statuses")
    return (allowed is None) or (status in allowed)


# ------------------------------------------------------------------ #
#  API publiques — appelées depuis JS et Python                       #
# ------------------------------------------------------------------ #

@frappe.whitelist()
def get_composer_payload(order_name: str, event_type: str = "message_libre") -> dict:
    """
    Retourne le payload pour pré-remplir le CommunicationComposer Frappe.

    Appelé depuis field_service_order.js via frappe.call().
    """
    fso = frappe.get_doc("Field Service Order", order_name)
    frappe.has_permission("Field Service Order", "email", fso, throw=True)

    cfg = EMAIL_EVENTS.get(event_type, EMAIL_EVENTS["message_libre"])
    recipients = _resolve_recipients(fso, cfg["recipients"])

    rendered: dict[str, str] = {}
    if cfg.get("template"):
        rendered = _render_template(cfg["template"], fso)

    base_subject = rendered.get("subject") or f"Intervention {fso.name}"
    tracked_subject = (
        f"[FSM-{event_type}] {base_subject}"
        if event_type != "message_libre"
        else base_subject
    )

    available_events = [
        {
            "value": k,
            "label": f"{v['icon']} {v['label']}",
            "available": _event_available(fso.status, k),
        }
        for k, v in EMAIL_EVENTS.items()
    ]

    return {
        "subject": tracked_subject,
        "recipients": ", ".join(recipients),
        "cc": "",
        "message": rendered.get("message", ""),
        "attach_pdf": cfg.get("attach_pdf", False),
        "print_format": "Field Service Order",
        "available_events": available_events,
        "last_emails": _get_recent_communications(order_name),
        "doc_status": fso.status,
    }


@frappe.whitelist()
def send_auto_email(order_name: str, event_type: str) -> dict:
    """
    Envoie un email automatiquement si toutes les conditions sont remplies.

    Retourne dict { sent: bool, reason: str }.
    """
    cfg = EMAIL_EVENTS.get(event_type)
    if not cfg:
        return {"sent": False, "reason": "event inconnu"}

    fso = frappe.get_doc("Field Service Order", order_name)

    # Fenêtre horaire
    if cfg.get("night_block") and _is_night():
        return {"sent": False, "reason": "horaire nuit — différé"}

    # Anti-doublon
    if _already_sent(order_name, event_type, cfg["cooldown_h"]):
        return {"sent": False, "reason": "doublon récent"}

    # Destinataires
    recipients = _resolve_recipients(fso, cfg["recipients"])
    if not recipients:
        return {"sent": False, "reason": "aucun destinataire résolu"}

    # Template
    rendered: dict[str, str] = {}
    if cfg.get("template"):
        rendered = _render_template(cfg["template"], fso)

    base_subject = rendered.get("subject") or f"Intervention {fso.name}"
    subject = f"[FSM-{event_type}] {base_subject}"
    message = rendered.get("message") or f"<p>Référence : {fso.name}</p>"

    # Pièce jointe PDF
    attachments = _build_pdf_attachment(order_name) if cfg.get("attach_pdf") else []

    try:
        frappe.sendmail(
            recipients=recipients,
            subject=subject,
            message=message,
            attachments=attachments,
            reference_doctype="Field Service Order",
            reference_name=order_name,
            now=True,
        )
        return {"sent": True, "recipients": recipients, "subject": subject}
    except Exception:
        frappe.log_error(
            frappe.get_traceback(),
            f"FSM Email: send_auto_email({event_type}) — {order_name}",
        )
        return {"sent": False, "reason": "erreur lors de l'envoi"}


def trigger_on_status_change(fso_name: str, old_status: str, new_status: str) -> None:
    """
    Mappe une transition de statut vers l'event email correspondant.
    Appelé depuis FieldServiceOrder.on_update (silencieux).
    """
    event_type = STATUS_TO_EVENT.get(new_status)
    if not event_type:
        return
    try:
        send_auto_email(fso_name, event_type)
    except Exception:
        frappe.log_error(
            frappe.get_traceback(),
            f"FSM Email: trigger_on_status_change({old_status}→{new_status}) — {fso_name}",
        )


def trigger_on_new(fso_name: str) -> None:
    """Envoie la confirmation de création. Appelé depuis after_insert."""
    try:
        send_auto_email(fso_name, "creation")
    except Exception:
        frappe.log_error(frappe.get_traceback(), f"FSM Email: trigger_on_new — {fso_name}")
