# Copyright (c) 2026, stevileshadow and contributors
# License: MIT

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import add_to_date, now_datetime


class FSMSLAPolicy(Document):

	def validate(self):
		self._ensure_single_default()
		self._validate_unique_priorities()

	def _ensure_single_default(self):
		if self.is_default:
			frappe.db.set_value(
				"FSM SLA Policy",
				{"is_default": 1, "name": ["!=", self.name]},
				"is_default", 0,
			)

	def _validate_unique_priorities(self):
		seen = set()
		for row in self.priorities:
			if row.priority in seen:
				frappe.throw(_("Priorité '{0}' définie en double dans la politique SLA.").format(row.priority))
			seen.add(row.priority)

	def get_deadlines_for_priority(self, priority, from_datetime=None):
		"""Calcule les dates limite de prise en charge et résolution."""
		from_dt = from_datetime or now_datetime()
		for row in self.priorities:
			if row.priority == priority:
				response_due = self._add_hours(from_dt, row.response_time_hours)
				resolution_due = self._add_hours(from_dt, row.resolution_time_hours)
				return {"response_due": response_due, "resolution_due": resolution_due}
		return {"response_due": None, "resolution_due": None}

	def _add_hours(self, from_dt, hours):
		"""Ajoute des heures en respectant (optionnellement) les heures ouvrables."""
		if not self.consider_working_hours:
			return add_to_date(from_dt, hours=hours)
		# Calcul simplifié heures ouvrables (sans jours fériés)
		return _add_working_hours(from_dt, hours, self)


def _add_working_hours(from_dt, hours_to_add, policy):
	"""Calcule une date en ajoutant N heures ouvrables."""
	from datetime import datetime, timedelta
	from frappe.utils import get_datetime

	dt = get_datetime(from_dt)
	remaining = float(hours_to_add)

	# Parse working hours
	start_h = int(str(policy.working_hours_start or "08:00:00")[:2])
	end_h = int(str(policy.working_hours_end or "18:00:00")[:2])
	work_day_hours = end_h - start_h

	# Working days mapping
	work_days = {
		"Lun-Ven": {0, 1, 2, 3, 4},
		"Lun-Sam": {0, 1, 2, 3, 4, 5},
		"Lun-Dim (24/7)": {0, 1, 2, 3, 4, 5, 6},
	}.get(policy.working_days or "Lun-Ven", {0, 1, 2, 3, 4})

	while remaining > 0:
		# Si on est hors des heures ouvrables, avancer au prochain créneau
		if dt.weekday() not in work_days:
			dt = dt.replace(hour=start_h, minute=0, second=0) + timedelta(days=1)
			continue
		if dt.hour < start_h:
			dt = dt.replace(hour=start_h, minute=0, second=0)
		if dt.hour >= end_h:
			dt = dt.replace(hour=start_h, minute=0, second=0) + timedelta(days=1)
			continue

		hours_left_today = end_h - dt.hour - dt.minute / 60
		if remaining <= hours_left_today:
			dt = dt + timedelta(hours=remaining)
			remaining = 0
		else:
			remaining -= hours_left_today
			dt = dt.replace(hour=start_h, minute=0, second=0) + timedelta(days=1)

	return dt


# ------------------------------------------------------------------ #
#  Helpers globaux                                                     #
# ------------------------------------------------------------------ #

def get_applicable_policy(fsm_team=None, activity_type=None, company=None):
	"""Retourne la politique SLA la plus spécifique pour un ordre."""
	filters = {"active": 1}

	# Cherche d'abord la politique la plus spécifique (équipe + type)
	if fsm_team and activity_type:
		policy = frappe.db.get_value(
			"FSM SLA Policy",
			{"active": 1, "fsm_team": fsm_team, "activity_type": activity_type},
			"name",
		)
		if policy:
			return frappe.get_doc("FSM SLA Policy", policy)

	# Puis équipe seule
	if fsm_team:
		policy = frappe.db.get_value(
			"FSM SLA Policy",
			{"active": 1, "fsm_team": fsm_team, "activity_type": ["is", "not set"]},
			"name",
		)
		if policy:
			return frappe.get_doc("FSM SLA Policy", policy)

	# Puis type d'activité seul
	if activity_type:
		policy = frappe.db.get_value(
			"FSM SLA Policy",
			{"active": 1, "fsm_team": ["is", "not set"], "activity_type": activity_type},
			"name",
		)
		if policy:
			return frappe.get_doc("FSM SLAPolicy", policy)

	# Sinon politique par défaut
	policy = frappe.db.get_value("FSM SLA Policy", {"active": 1, "is_default": 1}, "name")
	if policy:
		return frappe.get_doc("FSM SLA Policy", policy)

	return None
