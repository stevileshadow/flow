# Copyright (c) 2026, stevileshadow and contributors
# License: MIT

import frappe
from frappe import _
from frappe.model.document import Document


class FSMStage(Document):

	def validate(self):
		self.ensure_single_default()

	def ensure_single_default(self):
		"""Une seule étape peut être marquée comme défaut par type."""
		if self.is_default:
			frappe.db.set_value(
				"FSM Stage",
				{"stage_type": self.stage_type, "is_default": 1, "name": ["!=", self.name]},
				"is_default",
				0,
			)


@frappe.whitelist()
def get_stages_for_kanban(team=None, stage_type="Ordre"):
	"""Retourne les étapes ordonnées pour le pipeline Kanban."""
	filters = {"stage_type": stage_type}
	if team:
		# Filtrer sur les étapes assignées à l'équipe
		team_stages = frappe.get_all(
			"FSM Team Stage",
			filters={"parent": team},
			pluck="stage",
		)
		if team_stages:
			filters["name"] = ["in", team_stages]

	return frappe.get_all(
		"FSM Stage",
		filters=filters,
		fields=["name", "stage_name", "sequence", "color", "is_closed", "fold"],
		order_by="sequence asc",
	)
