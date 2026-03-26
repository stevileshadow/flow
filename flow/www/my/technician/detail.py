# Copyright (c) 2026, stevileshadow and contributors
# License: MIT
"""Page portail technicien — détail d'une intervention + saisie des heures."""

import frappe
from frappe import _


def get_context(context):
	if frappe.session.user == "Guest":
		frappe.throw(_("Connectez-vous pour accéder à votre espace technicien."), frappe.PermissionError)

	order_name = frappe.form_dict.get("name") or context.get("name")
	if not order_name:
		frappe.throw(_("Intervention introuvable."), frappe.DoesNotExistError)

	context.no_cache = 1
	context.show_sidebar = True

	employee = frappe.db.get_value("Employee", {"user_id": frappe.session.user}, "name")
	if not employee:
		frappe.throw(_("Aucun profil employé associé à votre compte."), frappe.PermissionError)

	# Vérifie que le technicien est assigné à cet ordre
	assignment = frappe.db.get_value(
		"FSM Order Technician",
		{"parent": order_name, "employee": employee},
		["is_lead"],
		as_dict=True,
	)
	is_lead = False
	if not assignment:
		# Peut aussi être le assigned_to (lead sans table enfant)
		lead_emp = frappe.db.get_value("Field Service Order", order_name, "assigned_to")
		if lead_emp != employee:
			frappe.throw(_("Vous n'êtes pas assigné à cette intervention."), frappe.PermissionError)
		is_lead = True
	else:
		is_lead = bool(assignment.is_lead)

	order = frappe.get_doc("Field Service Order", order_name)
	context.order = order
	context.title = order.title
	context.is_lead = is_lead

	# Droits de saisie selon le gabarit
	can_edit_timesheets = is_lead
	can_edit_parts = is_lead
	if not is_lead and order.fsm_template:
		tpl = frappe.get_cached_doc("FSM Template", order.fsm_template)
		can_edit_timesheets = bool(tpl.secondary_can_edit_timesheets)
		can_edit_parts = bool(tpl.secondary_can_edit_parts)

	context.can_edit_timesheets = can_edit_timesheets
	context.can_edit_parts = can_edit_parts

	# Feuille de temps existante du technicien (non consolidée)
	existing_ts_name = frappe.db.get_value(
		"FSM Technician Timesheet",
		{"field_service_order": order_name, "employee": employee, "is_consolidated": 0},
		"name",
	)
	if existing_ts_name:
		ts = frappe.get_doc("FSM Technician Timesheet", existing_ts_name)
		context.my_timesheet = ts
		context.my_timesheet_lines = ts.timesheets
	else:
		context.my_timesheet = None
		context.my_timesheet_lines = []

	return context
