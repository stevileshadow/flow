# Copyright (c) 2026, stevileshadow and contributors
# License: MIT
"""Portail technicien — Planning hebdomadaire, quarts à combler, pointage."""

import frappe
from frappe import _
from frappe.utils import add_days, get_first_day_of_week, getdate, today


def get_context(context):
    if frappe.session.user == "Guest":
        frappe.throw(_("Connectez-vous pour accéder à votre planning."), frappe.PermissionError)

    context.no_cache = 1
    context.show_sidebar = True
    context.title = _("Mon planning")

    employee = frappe.db.get_value("Employee", {"user_id": frappe.session.user}, "name")
    if not employee:
        context.no_employee = True
        return context

    context.no_employee = False
    context.employee = employee
    context.employee_name = frappe.db.get_value("Employee", employee, "employee_name")

    # Semaine affichée (navigation par query param ?week=YYYY-MM-DD)
    week_param = frappe.form_dict.get("week")
    week_start = getdate(week_param) if week_param else getdate(get_first_day_of_week(today()))
    week_end = add_days(week_start, 6)
    context.week_start = week_start
    context.week_end = week_end
    context.prev_week = str(add_days(week_start, -7))
    context.next_week = str(add_days(week_start, 7))

    # Jours de la semaine avec labels
    context.days = [
        {"date": str(add_days(week_start, i)),
         "label": ["Lun", "Mar", "Mer", "Jeu", "Ven", "Sam", "Dim"][i]}
        for i in range(7)
    ]

    # FSO planifiés de la semaine
    orders = frappe.get_all(
        "Field Service Order",
        filters={
            "assigned_to": employee,
            "scheduled_date": ["between", [str(week_start), str(week_end)]],
            "status": ["not in", ["Annulé"]],
        },
        fields=[
            "name", "title", "status", "priority", "scheduled_date",
            "scheduled_time", "customer_name", "fsm_location",
            "activity_type", "actual_start", "actual_end",
        ],
        order_by="scheduled_date asc, scheduled_time asc",
    )
    # Index par date pour affichage calendrier
    orders_by_date = {}
    for o in orders:
        d = str(o.scheduled_date)
        orders_by_date.setdefault(d, []).append(o)
    context.orders_by_date = orders_by_date
    context.total_orders = len(orders)

    # Quarts à combler disponibles cette semaine
    open_shifts_raw = frappe.get_all(
        "FSM Open Shift",
        filters={
            "status": "Ouvert",
            "shift_date": ["between", [str(week_start), str(week_end)]],
        },
        fields=["name", "title", "shift_date", "from_time", "to_time",
                "fsm_location", "description", "headcount"],
        order_by="shift_date asc",
    )
    context.open_shifts = [dict(s) for s in open_shifts_raw]

    # Congés ERPNext de la semaine
    context.leaves = []
    try:
        context.leaves = frappe.get_all(
            "Leave Application",
            filters={
                "employee": employee,
                "status": "Approved",
                "from_date": ["<=", str(week_end)],
                "to_date": [">=", str(week_start)],
                "docstatus": 1,
            },
            fields=["name", "leave_type", "from_date", "to_date"],
        )
    except Exception:
        pass

    # Derniers pointages GPS (5 derniers)
    context.recent_punches = frappe.get_all(
        "FSM Geolocation Punch",
        filters={"employee": employee},
        fields=["name", "punch_type", "punch_time", "field_service_order",
                "is_valid", "within_radius", "distance_from_site"],
        order_by="punch_time desc",
        limit=5,
    )

    # Disponibilités déclarées du technicien
    avail_name = frappe.db.get_value(
        "FSM Technician Availability", {"employee": employee}, "name"
    )
    context.has_availability = bool(avail_name)
    context.availability_link = (
        frappe.utils.get_link_to_form("FSM Technician Availability", avail_name)
        if avail_name else None
    )

    return context
