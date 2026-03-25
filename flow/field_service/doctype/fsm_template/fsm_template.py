# Copyright (c) 2026, stevileshadow and contributors
# License: MIT

import frappe
from frappe import _
from frappe.model.document import Document


class FSMTemplate(Document):
	pass


# ------------------------------------------------------------------ #
#  API publique                                                        #
# ------------------------------------------------------------------ #

@frappe.whitelist()
def apply_template_to_order(order_name, template_name):
	"""Applique un modèle à un ordre d'intervention existant.

	Remplit : titre, activity_type, billing_type, fsm_team,
	          scheduled_duration, priority, description,
	          instructions (internal_notes), et les pièces par défaut.
	Ne remplace que les champs vides (sauf les pièces qui sont ajoutées).
	"""
	order = frappe.get_doc("Field Service Order", order_name)
	tpl = frappe.get_doc("FSM Template", template_name)

	changed = False

	# --- Champs simples : ne remplace que si vide ---
	simple_fields = {
		"activity_type": tpl.activity_type,
		"billing_type": tpl.billing_type,
		"fsm_team": tpl.fsm_team,
		"priority": tpl.priority,
	}
	for field, value in simple_fields.items():
		if value and not order.get(field):
			order.set(field, value)
			changed = True

	# Durée : prend la valeur du modèle si non définie
	if tpl.scheduled_duration and not order.scheduled_duration:
		order.scheduled_duration = tpl.scheduled_duration
		changed = True

	# Description : concatène si déjà renseignée, sinon remplace
	if tpl.description:
		if order.description:
			order.description = order.description + "<hr>" + tpl.description
		else:
			order.description = tpl.description
		changed = True

	# Instructions / checklist → internal_notes
	if tpl.instructions:
		if order.internal_notes:
			order.internal_notes = order.internal_notes + "<hr>" + tpl.instructions
		else:
			order.internal_notes = tpl.instructions
		changed = True

	# --- Pièces : ajoute les lignes du modèle ---
	if tpl.default_parts:
		for part in tpl.default_parts:
			order.append("parts", {
				"item_code": part.item_code,
				"item_name": part.item_name,
				"qty": part.qty,
				"uom": part.uom,
			})
		changed = True

	if changed:
		order.save(ignore_permissions=True)
		frappe.msgprint(
			_("Modèle '{0}' appliqué avec succès.").format(tpl.template_name),
			alert=True,
			indicator="green",
		)

	return {
		"activity_type": order.activity_type,
		"billing_type": order.billing_type,
		"fsm_team": order.fsm_team,
		"priority": order.priority,
		"scheduled_duration": order.scheduled_duration,
		"description": order.description,
		"internal_notes": order.internal_notes,
		"parts_count": len(order.parts),
	}


@frappe.whitelist()
def get_templates_for_type(activity_type=None):
	"""Retourne les modèles filtrés par type d'activité."""
	filters = {"active": 1}
	if activity_type:
		filters["activity_type"] = activity_type
	return frappe.get_all(
		"FSM Template",
		filters=filters,
		fields=[
			"name", "template_name", "activity_type", "billing_type",
			"scheduled_duration", "priority", "fsm_team",
		],
		order_by="template_name asc",
	)
