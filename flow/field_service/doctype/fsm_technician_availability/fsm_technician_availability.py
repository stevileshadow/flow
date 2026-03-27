# Copyright (c) 2026, stevileshadow and contributors
# License: MIT
"""Disponibilités hebdomadaires + exceptions ponctuelles d'un technicien."""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import getdate


_DAYS = ["Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi", "Samedi", "Dimanche"]
_WEEKDAY_MAP = {d: i for i, d in enumerate(_DAYS)}


class FSMTechnicianAvailability(Document):

    def validate(self):
        self._check_duplicate_days()
        self._check_duplicate_exceptions()

    def _check_duplicate_days(self):
        seen = set()
        for row in self.week_schedule or []:
            if row.day_of_week in seen:
                frappe.throw(
                    _("Jour en double dans le gabarit hebdomadaire : {0}").format(row.day_of_week)
                )
            seen.add(row.day_of_week)

    def _check_duplicate_exceptions(self):
        seen = set()
        for row in self.exceptions or []:
            d = str(row.exception_date)
            if d in seen:
                frappe.throw(
                    _("Date en double dans les exceptions : {0}").format(d)
                )
            seen.add(d)


# ── API publiques ──────────────────────────────────────────────────────────────

@frappe.whitelist()
def is_available(employee, date, from_time=None, to_time=None):
    """
    Retourne True si le technicien est disponible à la date/plage donnée.
    Vérifie d'abord les exceptions, puis le gabarit hebdomadaire.
    Vérifie aussi les congés ERPNext (Leave Application).
    """
    date = getdate(date)

    # 1. Congés ERPNext
    if _has_approved_leave(employee, date):
        return False

    # 2. Exception ponctuelle
    rec = frappe.db.get_value(
        "FSM Technician Availability", employee, "name", order_by=None
    )
    if not rec:
        return True  # Pas de fiche = disponible par défaut

    avail = frappe.get_doc("FSM Technician Availability", rec)
    date_str = str(date)
    for exc in avail.exceptions or []:
        if str(exc.exception_date) == date_str:
            if not exc.is_available:
                return False
            if from_time and exc.from_time and exc.to_time:
                return _time_overlap(from_time, to_time, exc.from_time, exc.to_time)
            return bool(exc.is_available)

    # 3. Gabarit hebdomadaire (weekday 0=lundi)
    weekday = date.weekday()
    day_name = _DAYS[weekday]
    for row in avail.week_schedule or []:
        if row.day_of_week == day_name:
            if not row.is_available:
                return False
            if from_time and row.from_time and row.to_time:
                return _time_overlap(from_time, to_time, row.from_time, row.to_time)
            return True

    return True  # Jour non défini dans le gabarit = disponible


def _has_approved_leave(employee, date):
    """Vérifie si une Leave Application approuvée couvre cette date."""
    try:
        count = frappe.db.count(
            "Leave Application",
            filters={
                "employee": employee,
                "status": "Approved",
                "from_date": ["<=", date],
                "to_date": [">=", date],
                "docstatus": 1,
            },
        )
        return count > 0
    except Exception:
        return False


def _time_overlap(req_from, req_to, avail_from, avail_to):
    """Retourne True si la plage demandée est incluse dans la plage disponible."""
    if not req_to:
        return str(req_from) >= str(avail_from)
    return str(req_from) >= str(avail_from) and str(req_to) <= str(avail_to)
