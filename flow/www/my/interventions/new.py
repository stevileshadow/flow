# Copyright (c) 2026, stevileshadow and contributors
# License: MIT
"""Page portail client — nouvelle demande d'intervention."""

import frappe
from frappe import _


def get_context(context):
	if frappe.session.user == "Guest":
		frappe.throw(_("Connectez-vous pour soumettre une demande."), frappe.PermissionError)

	from flow.field_service.www.my.interventions import _get_customer_for_user
	customer = _get_customer_for_user(frappe.session.user)

	context.no_cache = 1
	context.show_sidebar = True
	context.title = _("Nouvelle demande de service")
	context.customer = customer
	context.no_customer = not bool(customer)
	context.activity_types = frappe.get_all(
		"Field Service Activity Type",
		fields=["activity_type_name as value", "activity_type_name as label"],
		order_by="activity_type_name asc",
	)
	# Lieux du client
	context.locations = frappe.get_all(
		"FSM Location",
		filters={"customer": customer, "active": 1} if customer else {"active": 0},
		fields=["name as value", "location_name as label", "complete_name"],
		order_by="location_name asc",
	)
	return context


@frappe.whitelist(allow_guest=False)
def submit_service_request(title, description, activity_type=None, fsm_location=None, priority="Normal"):
	"""Crée un Field Service Order depuis le portail client."""
	from flow.field_service.www.my.interventions import _get_customer_for_user
	customer = _get_customer_for_user(frappe.session.user)
	if not customer:
		frappe.throw(_("Aucun compte client associé à votre profil."))

	order = frappe.get_doc({
		"doctype": "Field Service Order",
		"title": title,
		"description": description,
		"activity_type": activity_type,
		"fsm_location": fsm_location,
		"priority": priority,
		"customer": customer,
		"status": "Nouveau",
	})
	order.insert(ignore_permissions=False)

	frappe.msgprint(
		_("Demande {0} créée avec succès. Notre équipe vous contactera prochainement.").format(
			order.name
		),
		alert=True,
		indicator="green",
	)
	return {"name": order.name}
