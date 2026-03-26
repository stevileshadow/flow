# Copyright (c) 2026, stevileshadow and contributors
# License: MIT
"""Page portail technicien — liste des interventions assignées."""

import frappe
from frappe import _


def get_context(context):
	if frappe.session.user == "Guest":
		frappe.throw(_("Connectez-vous pour accéder à votre espace technicien."), frappe.PermissionError)

	context.no_cache = 1
	context.show_sidebar = True
	context.title = _("Mon espace technicien")

	employee = frappe.db.get_value("Employee", {"user_id": frappe.session.user}, "name")
	if not employee:
		context.orders = []
		context.no_employee = True
		return context

	context.employee = employee
	context.no_employee = False

	# Ordres où le technicien est lead (assigned_to) ou secondaire (dans FSM Order Technician)
	lead_orders = frappe.get_all(
		"Field Service Order",
		filters={
			"assigned_to": employee,
			"status": ["not in", ["Annulé"]],
			"docstatus": ["!=", 2],
		},
		fields=["name"],
		pluck="name",
	)

	secondary_orders = frappe.get_all(
		"FSM Order Technician",
		filters={"employee": employee},
		fields=["parent"],
		pluck="parent",
	)

	order_names = list(set(lead_orders + secondary_orders))
	if not order_names:
		context.orders = []
		return context

	status_filter = frappe.form_dict.get("status")
	filters = {"name": ["in", order_names], "docstatus": ["!=", 2]}
	if status_filter:
		filters["status"] = status_filter

	context.orders = frappe.get_all(
		"Field Service Order",
		filters=filters,
		fields=[
			"name", "title", "status", "priority", "customer_name",
			"scheduled_date", "scheduled_time", "activity_type",
			"fsm_location", "assigned_to", "assigned_to_name",
		],
		order_by="scheduled_date asc, scheduled_time asc",
		limit=100,
	)

	# Marquer si le technicien est lead sur chaque ordre
	for o in context.orders:
		o["is_lead"] = o.assigned_to == employee

	context.active_filter = status_filter or "all"
	return context
