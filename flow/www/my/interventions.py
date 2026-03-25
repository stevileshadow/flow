# Copyright (c) 2026, stevileshadow and contributors
# License: MIT
"""Page portail client — liste des interventions."""

import frappe
from frappe import _


def get_context(context):
	if frappe.session.user == "Guest":
		frappe.throw(_("Connectez-vous pour accéder à vos interventions."), frappe.PermissionError)

	context.no_cache = 1
	context.show_sidebar = True
	context.title = _("Mes interventions")

	customer = _get_customer_for_user(frappe.session.user)
	if not customer:
		context.orders = []
		context.no_customer = True
		return context

	context.customer = customer
	context.no_customer = False

	filters = {"customer": customer, "docstatus": ["!=", 2]}
	status_filter = frappe.form_dict.get("status")
	if status_filter:
		filters["status"] = status_filter

	context.orders = frappe.get_all(
		"Field Service Order",
		filters=filters,
		fields=[
			"name", "title", "status", "fsm_stage", "priority",
			"scheduled_date", "scheduled_time", "assigned_to_name",
			"activity_type", "fsm_location", "actual_start", "actual_end",
			"sla_resolution_due", "sla_resolution_status", "customer_signature",
			"total_amount", "invoice",
		],
		order_by="scheduled_date desc",
		limit=50,
	)

	context.status_counts = _get_status_counts(customer)
	context.active_filter = status_filter or "all"
	return context


def _get_customer_for_user(user):
	"""Retrouve le Customer lié à l'utilisateur connecté."""
	contact = frappe.db.get_value("Contact", {"user": user}, "name")
	if contact:
		links = frappe.get_all(
			"Dynamic Link",
			filters={"parent": contact, "link_doctype": "Customer"},
			fields=["link_name"],
			limit=1,
		)
		if links:
			return links[0].link_name
	# Fallback : cherche par email
	email = frappe.db.get_value("User", user, "email")
	customer = frappe.db.get_value("Customer", {"email_id": email}, "name")
	return customer


def _get_status_counts(customer):
	all_orders = frappe.get_all(
		"Field Service Order",
		filters={"customer": customer, "docstatus": ["!=", 2]},
		fields=["status"],
	)
	counts = {"all": len(all_orders)}
	for o in all_orders:
		counts[o.status] = counts.get(o.status, 0) + 1
	return counts
