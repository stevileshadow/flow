# Copyright (c) 2026, stevileshadow and contributors
# License: MIT
"""Quart à combler — publié par le gestionnaire, réclamé par les techniciens."""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import now_datetime


class FSMOpenShift(Document):

    def validate(self):
        if self.from_time and self.to_time and str(self.from_time) >= str(self.to_time):
            frappe.throw(_("L'heure de fin doit être après l'heure de début."))

    def on_update(self):
        if self.status == "Ouvert" and self.has_value_changed("status"):
            self._notify_available_technicians()

    def _notify_available_technicians(self):
        """Notifie par email les techniciens disponibles sur ce quart."""
        try:
            from flow.field_service.hr_engine import notify_open_shift_to_team
            notify_open_shift_to_team(self.name)
        except Exception:
            frappe.log_error(frappe.get_traceback(), f"FSM Open Shift: notify — {self.name}")
