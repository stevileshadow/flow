# Copyright (c) 2026, stevileshadow and contributors
# License: MIT

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt, now_datetime, time_diff_in_hours


class FSMTechnicianTimesheet(Document):

	def validate(self):
		self._verify_assignment()
		self._calculate_lines()

	def _verify_assignment(self):
		"""Vérifie que l'employé est bien assigné à l'ordre comme technicien secondaire."""
		assigned = frappe.db.exists(
			"FSM Order Technician",
			{"parent": self.field_service_order, "employee": self.employee},
		)
		if not assigned:
			frappe.throw(
				_("{0} n'est pas assigné à l'ordre {1}.").format(
					self.employee_name or self.employee, self.field_service_order
				)
			)

	def _calculate_lines(self):
		for row in self.timesheets:
			if row.from_time and row.to_time:
				row.hours = flt(time_diff_in_hours(row.to_time, row.from_time), 2)
			if row.is_billable and not row.is_break:
				row.billing_hours = row.billing_hours or row.hours
				row.billing_amount = flt(row.billing_hours) * flt(row.hourly_rate)
			else:
				row.billing_hours = 0
				row.billing_amount = 0
