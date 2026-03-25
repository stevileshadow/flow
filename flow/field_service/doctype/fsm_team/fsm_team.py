# Copyright (c) 2026, stevileshadow and contributors
# License: MIT

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import today


class FSMTeam(Document):

	def get_open_orders_count(self):
		return frappe.db.count(
			"Field Service Order",
			{
				"fsm_team": self.name,
				"status": ["not in", ["Terminé", "Facturé", "Annulé"]],
			},
		)

	def get_unassigned_orders_count(self):
		return frappe.db.count(
			"Field Service Order",
			{
				"fsm_team": self.name,
				"assigned_to": ["is", "not set"],
				"status": ["not in", ["Terminé", "Facturé", "Annulé"]],
			},
		)

	def get_today_orders_count(self):
		return frappe.db.count(
			"Field Service Order",
			{
				"fsm_team": self.name,
				"scheduled_date": today(),
				"status": ["not in", ["Terminé", "Facturé", "Annulé"]],
			},
		)

	def get_member_user_ids(self):
		return [
			m.user_id
			for m in self.members
			if m.user_id
		]


@frappe.whitelist()
def get_team_dashboard(team_name):
	"""Données de tableau de bord pour une équipe."""
	team = frappe.get_doc("FSM Team", team_name)
	return {
		"open_orders": team.get_open_orders_count(),
		"unassigned": team.get_unassigned_orders_count(),
		"today": team.get_today_orders_count(),
		"members": len(team.members),
	}
