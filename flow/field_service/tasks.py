# Copyright (c) 2026, stevileshadow and contributors
# License: MIT
"""Tâches planifiées et événements doc pour le module Field Service."""

import frappe
from frappe import _
from frappe.utils import add_days, getdate, now_datetime, today


def send_daily_reminders():
	"""Envoie une notification à chaque technicien pour ses interventions du jour."""
	orders = frappe.get_all(
		"Field Service Order",
		filters={
			"scheduled_date": today(),
			"status": ["in", ["Planifié", "En cours"]],
			"assigned_to": ["is", "set"],
		},
		fields=["name", "title", "assigned_to", "assigned_to_name", "customer_name",
		        "scheduled_time", "address_display"],
	)

	# Regrouper par technicien
	by_tech = {}
	for order in orders:
		by_tech.setdefault(order.assigned_to, []).append(order)

	for employee, tech_orders in by_tech.items():
		user = frappe.db.get_value("Employee", employee, "user_id")
		if not user:
			continue

		lines = "\n".join(
			f"- {o.title} ({o.customer_name}) à {o.scheduled_time or 'heure non définie'}"
			for o in tech_orders
		)
		frappe.sendmail(
			recipients=[user],
			subject=_("Vos interventions du jour — {0}").format(today()),
			message=_(
				"Bonjour {0},\n\nVoici vos interventions planifiées aujourd'hui :\n\n{1}\n\n"
				"Bonne journée !"
			).format(tech_orders[0].assigned_to_name, lines),
		)


def flag_overdue_orders():
	"""Marque les interventions non démarrées en retard (past scheduled_date, still Planifié)."""
	overdue = frappe.get_all(
		"Field Service Order",
		filters={
			"scheduled_date": ["<", today()],
			"status": "Planifié",
		},
		fields=["name"],
	)
	for o in overdue:
		frappe.db.set_value("Field Service Order", o.name, "status", "En cours")
	if overdue:
		frappe.db.commit()


def update_sla_statuses():
	"""Recalcule les statuts SLA pour toutes les interventions actives (scheduler horaire)."""
	active_orders = frappe.get_all(
		"Field Service Order",
		filters={
			"status": ["not in", ["Terminé", "Facturé", "Annulé"]],
			"sla_resolution_due": ["is", "set"],
		},
		fields=["name"],
	)
	for o in active_orders:
		doc = frappe.get_doc("Field Service Order", o.name)
		doc.update_sla_status()
		doc.db_set("sla_response_status", doc.sla_response_status, update_modified=False)
		doc.db_set("sla_resolution_status", doc.sla_resolution_status, update_modified=False)
	if active_orders:
		frappe.db.commit()


def notify_technician_on_assignment(doc, method=None):
	"""Notifie le technicien lors de la soumission de l'ordre."""
	if not doc.assigned_to:
		return
	user = frappe.db.get_value("Employee", doc.assigned_to, "user_id")
	if not user:
		return
	frappe.sendmail(
		recipients=[user],
		subject=_("Nouvelle intervention assignée : {0}").format(doc.title),
		message=_(
			"Bonjour {0},\n\n"
			"Une intervention vous a été assignée :\n\n"
			"  Référence : {1}\n"
			"  Client    : {2}\n"
			"  Date      : {3}\n"
			"  Priorité  : {4}\n\n"
			"Connectez-vous à Flow pour consulter les détails."
		).format(
			doc.assigned_to_name,
			doc.name,
			doc.customer_name,
			doc.scheduled_date or _("À planifier"),
			doc.priority,
		),
	)


def notify_cancellation(doc, method=None):
	"""Notifie le technicien en cas d'annulation."""
	if not doc.assigned_to:
		return
	user = frappe.db.get_value("Employee", doc.assigned_to, "user_id")
	if not user:
		return
	frappe.sendmail(
		recipients=[user],
		subject=_("Intervention annulée : {0}").format(doc.title),
		message=_(
			"Bonjour {0},\n\nL'intervention {1} ({2}) a été annulée."
		).format(doc.assigned_to_name, doc.name, doc.title),
	)
