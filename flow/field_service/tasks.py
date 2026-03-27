# Copyright (c) 2026, stevileshadow and contributors
# License: MIT
"""Tâches planifiées et événements doc pour le module Field Service."""

import frappe
from frappe import _
from frappe.utils import add_days, getdate, now_datetime, today


def send_daily_reminders():
	"""
	Rappel quotidien pour chaque technicien : utilise l'email engine
	pour bénéficier du template riche + anti-doublon.
	"""
	from flow.field_service.email_engine import send_auto_email

	orders = frappe.get_all(
		"Field Service Order",
		filters={
			"scheduled_date": today(),
			"status": ["in", ["Planifié", "En cours"]],
			"assigned_to": ["is", "set"],
		},
		fields=["name"],
	)
	for o in orders:
		try:
			send_auto_email(o.name, "assignation")
		except Exception:
			frappe.log_error(frappe.get_traceback(), f"FSM tasks: daily reminder — {o.name}")


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


def generate_preventive_maintenance_orders():
	"""Crée automatiquement un ordre de maintenance préventive pour chaque équipement
	dont la date de prochain entretien est atteinte et qui n'a pas déjà un ordre PM ouvert."""
	overdue = frappe.get_all(
		"FSM Equipment",
		filters={
			"next_service_date": ["<=", today()],
			"active": 1,
			"status": ["not in", ["Hors service", "Retiré"]],
		},
		fields=["name", "equipment_name", "fsm_location", "customer",
		        "customer_name", "assigned_to", "company"],
	)
	if not overdue:
		return

	default_company = frappe.defaults.get_global_default("company")
	created = 0

	for eq in overdue:
		# Ne crée pas si un ordre PM ouvert existe déjà pour cet équipement
		has_open = frappe.db.exists(
			"Field Service Order",
			{
				"fsm_equipment": eq.name,
				"is_preventive_maintenance": 1,
				"status": ["not in", ["Terminé", "Facturé", "Annulé"]],
			},
		)
		if has_open:
			continue

		order = frappe.new_doc("Field Service Order")
		order.title = _("Maintenance préventive — {0}").format(eq.equipment_name)
		order.is_preventive_maintenance = 1
		order.fsm_equipment = eq.name
		order.fsm_location = eq.fsm_location
		order.customer = eq.customer
		order.customer_name = eq.customer_name
		order.company = eq.company or default_company
		order.priority = "Normal"
		order.scheduled_date = today()
		if eq.assigned_to:
			order.assigned_to = eq.assigned_to

		try:
			order.insert(ignore_permissions=True)
			created += 1
		except Exception:
			frappe.log_error(frappe.get_traceback(), "FSM: Échec création ordre PM")

	if created:
		frappe.db.commit()
		frappe.logger().info(f"[FSM] {created} ordre(s) de maintenance préventive créé(s)")


def send_previsit_notifications():
	"""D — Envoie un rappel au client la veille de son intervention (statut Planifié)."""
	from flow.field_service.email_engine import send_auto_email

	tomorrow = add_days(today(), 1)
	orders = frappe.get_all(
		"Field Service Order",
		filters={
			"scheduled_date": tomorrow,
			"status": "Planifié",
		},
		fields=["name"],
	)
	for order in orders:
		try:
			send_auto_email(order.name, "pre_visite")
		except Exception:
			frappe.log_error(frappe.get_traceback(), f"FSM tasks: pre-visit — {order.name}")


def escalate_breached_sla_orders():
	"""F — Notifie le Responsable d'équipe (une seule fois) quand le SLA résolution est dépassé."""
	breached = frappe.get_all(
		"Field Service Order",
		filters={
			"sla_resolution_status": "Dépassé",
			"sla_escalated": 0,
			"status": ["not in", ["Terminé", "Facturé", "Annulé"]],
		},
		fields=["name", "title", "customer_name", "fsm_team",
		        "assigned_to_name", "sla_resolution_due", "priority"],
	)
	if not breached:
		return

	for order in breached:
		manager_emails = []

		# Responsable(s) de l'équipe assignée
		if order.fsm_team:
			managers = frappe.get_all(
				"FSM Team Member",
				filters={"parent": order.fsm_team, "role_in_team": "Responsable"},
				fields=["user_id"],
			)
			manager_emails = [m.user_id for m in managers if m.user_id]

		# Fallback : tous les utilisateurs avec le rôle Field Service Manager
		if not manager_emails:
			manager_emails = [
				r.parent for r in frappe.get_all(
					"Has Role",
					filters={"role": "Field Service Manager", "parenttype": "User"},
					fields=["parent"],
				)
			]

		if not manager_emails:
			continue

		frappe.sendmail(
			recipients=manager_emails,
			subject=_("[ALERTE SLA] Intervention {0} — délai de résolution dépassé").format(order.name),
			message=_(
				"Bonjour,\n\n"
				"Le SLA de résolution est dépassé pour l'intervention suivante :\n\n"
				"  Référence : {0}\n"
				"  Titre     : {1}\n"
				"  Client    : {2}\n"
				"  Priorité  : {3}\n"
				"  Technicien: {4}\n"
				"  Échéance  : {5}\n\n"
				"Veuillez prendre les mesures nécessaires."
			).format(
				order.name,
				order.title,
				order.customer_name,
				order.priority,
				order.assigned_to_name or _("Non assigné"),
				order.sla_resolution_due,
			),
		)
		frappe.db.set_value(
			"Field Service Order", order.name, "sla_escalated", 1,
			update_modified=False,
		)

	frappe.db.commit()


def send_monthly_decompte():
	"""1er de chaque mois — génère le décompte du mois précédent et l'envoie par email."""
	import datetime
	today = datetime.date.today()
	if today.month == 1:
		month, year = 12, today.year - 1
	else:
		month, year = today.month - 1, today.year

	try:
		from flow.field_service.api import _collect_data, _build_xlsx
		settings = frappe.get_single("FSM Settings")
		data = _collect_data(month, year)
		xlsx = _build_xlsx(data, month, year)
	except Exception:
		frappe.log_error(frappe.get_traceback(), "FSM: Erreur génération décompte mensuel")
		return

	months_fr = ["", "Janvier", "Février", "Mars", "Avril", "Mai", "Juin",
	             "Juillet", "Août", "Septembre", "Octobre", "Novembre", "Décembre"]
	period = f"{months_fr[month]} {year}"
	filename = f"decompte_{year}_{month:02d}.xlsx"
	s = data["summary"]

	body = _(
		"Bonjour,\n\nVeuillez trouver en pièce jointe le décompte mensuel Field Service pour {0}.\n\n"
		"Résumé :\n"
		"  Interventions : {1} (terminées : {2}, facturées : {3})\n"
		"  Heures totales : {4} h\n"
		"  Chiffre d'affaires : {5} €\n"
		"  Techniciens actifs : {6}\n\n"
		"Rapport complet disponible sur : {7}/my/decompte?month={8}&year={9}"
	).format(
		period, s["nb_orders"], s["nb_completed"], s["nb_invoiced"],
		round(s["total_hours"], 1), round(s["total_revenue"], 2),
		s["nb_technicians"],
		frappe.utils.get_url(), month, year,
	)

	def _recipients_from_field(field_name):
		raw = getattr(settings, field_name, None) or ""
		return [e.strip() for e in raw.splitlines() if "@" in e.strip()]

	all_recipients = list(set(
		_recipients_from_field("decompte_recipients_hr")
		+ _recipients_from_field("decompte_recipients_accounts")
		+ _recipients_from_field("decompte_recipients_projects")
	))

	# Fallback : tous les Field Service Managers
	if not all_recipients:
		all_recipients = [
			r.parent for r in frappe.get_all(
				"Has Role",
				filters={"role": "Field Service Manager", "parenttype": "User"},
				fields=["parent"],
			)
		]

	if all_recipients:
		frappe.sendmail(
			recipients=all_recipients,
			subject=_(f"[FSM] Décompte mensuel — {period}"),
			message=body,
			attachments=[{"fname": filename, "fcontent": xlsx}],
		)
		frappe.logger().info(f"[FSM] Décompte {period} envoyé à {len(all_recipients)} destinataire(s)")


def notify_technician_on_assignment(doc, method=None):
	"""Notifie le(s) technicien(s) lors de la soumission de l'ordre."""
	recipients = []

	# Technicien lead / assigné principal
	if doc.assigned_to:
		user = frappe.db.get_value("Employee", doc.assigned_to, "user_id")
		if user:
			recipients.append((user, doc.assigned_to_name or doc.assigned_to))

	# Techniciens secondaires (table enfant FSM Order Technician)
	if getattr(doc, "technicians", None):
		for tech in doc.technicians:
			if tech.employee == doc.assigned_to:
				continue  # déjà notifié via assigned_to
			sec_user = tech.user_id or frappe.db.get_value("Employee", tech.employee, "user_id")
			if sec_user:
				recipients.append((sec_user, tech.employee_name or tech.employee))

	for user, name in recipients:
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
				"Connectez-vous à Flow pour consulter les détails.\n"
				"Portail technicien : {5}/my/technician"
			).format(
				name,
				doc.name,
				doc.customer_name,
				doc.scheduled_date or _("À planifier"),
				doc.priority,
				frappe.utils.get_url(),
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


def notify_customer_on_status_change(doc, method=None):
	"""Notifie le client par email à chaque changement de statut de son intervention."""
	# Détecte l'ancien statut avant sauvegarde
	old_status = None
	if getattr(doc, "_doc_before_save", None):
		old_status = doc._doc_before_save.status

	if old_status == doc.status:
		return  # Pas de changement

	# Cherche l'email du client : d'abord sur l'ordre, puis sur le Contact
	customer_email = doc.contact_email or None
	if not customer_email and doc.contact_person:
		customer_email = frappe.db.get_value("Contact", doc.contact_person, "email_id")
	if not customer_email:
		return

	status_messages = {
		"Planifié": _("Votre intervention a été planifiée."),
		"En cours": _("Votre intervention est en cours — le technicien est sur place."),
		"En attente de pièces": _("Votre intervention est en attente de pièces. Nous vous tiendrons informé."),
		"Terminé": _("Votre intervention est terminée. Merci de votre confiance."),
		"Facturé": _("Votre facture a été émise."),
		"Annulé": _("Votre intervention a été annulée. N'hésitez pas à nous contacter."),
	}

	message_detail = status_messages.get(doc.status)
	if not message_detail:
		return

	frappe.sendmail(
		recipients=[customer_email],
		subject=_("Mise à jour de votre intervention — {0}").format(doc.title),
		message=_(
			"Bonjour {0},\n\n"
			"{1}\n\n"
			"  Référence : {2}\n"
			"  Nouveau statut : {3}\n"
			"  Date planifiée : {4}\n\n"
			"Consultez les détails sur votre espace client."
		).format(
			doc.customer_name or _("Client"),
			message_detail,
			doc.name,
			doc.status,
			doc.scheduled_date or _("À définir"),
		),
	)


# ════════════════════════════════════════════════════════════════════════════
#  Jobs planifiés — Automation cross-modules ERPNext
# ════════════════════════════════════════════════════════════════════════════

def sync_pending_erp_timesheets():
	"""
	Batch nocturne (02h00) — rattrape les Timesheets ERPNext manquantes pour
	les FSO clôturés depuis moins de 7 jours.
	Délègue à automation.sync_pending_erp_timesheets().
	"""
	try:
		from flow.field_service.automation import sync_pending_erp_timesheets as _run
		_run()
	except Exception:
		frappe.log_error(frappe.get_traceback(), "FSM tasks: sync_pending_erp_timesheets")


def auto_close_stale_orders():
	"""
	Batch nocturne (02h00) — ferme automatiquement les ordres bloqués en
	'En cours' selon le délai configuré dans FSM Settings.
	Délègue à automation.auto_close_stale_orders().
	"""
	try:
		from flow.field_service.automation import auto_close_stale_orders as _run
		_run()
	except Exception:
		frappe.log_error(frappe.get_traceback(), "FSM tasks: auto_close_stale_orders")


def auto_generate_mandate_invoices():
	"""
	Batch matin (06h00) — génère les Sales Invoices pour les mandats dont
	tous les FSO sont clôturés et sans facture.
	Délègue à automation.auto_generate_mandate_invoices().
	"""
	try:
		from flow.field_service.automation import auto_generate_mandate_invoices as _run
		_run()
	except Exception:
		frappe.log_error(frappe.get_traceback(), "FSM tasks: auto_generate_mandate_invoices")


# ════════════════════════════════════════════════════════════════════════════
#  Jobs planifiés — RH (quarts, disponibilités, horaire automatique)
# ════════════════════════════════════════════════════════════════════════════

def notify_open_shifts_daily():
	"""
	Batch matin (07h00) — notifie les techniciens disponibles pour les
	quarts à combler des 7 prochains jours.
	Délègue à hr_engine.notify_open_shifts_daily().
	"""
	try:
		from flow.field_service.hr_engine import notify_open_shifts_daily as _run
		_run()
	except Exception:
		frappe.log_error(frappe.get_traceback(), "FSM tasks: notify_open_shifts_daily")


def auto_schedule_unassigned_fsos():
	"""
	Batch matin (07h00) — assigne automatiquement les FSO sans technicien
	planifiés dans les 3 prochains jours (horaire automatique).
	Délègue à hr_engine.auto_schedule_unassigned_fsos().
	"""
	try:
		from flow.field_service.hr_engine import auto_schedule_unassigned_fsos as _run
		_run()
	except Exception:
		frappe.log_error(frappe.get_traceback(), "FSM tasks: auto_schedule_unassigned_fsos")
