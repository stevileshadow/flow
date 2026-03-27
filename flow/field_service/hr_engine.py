# Copyright (c) 2026, stevileshadow and contributors
# License: MIT
"""
flow.field_service.hr_engine
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Moteur RH Field Service — inspiré de Agendrix (Québec).

Fonctionnalités :
  1. Disponibilités techniciens (gabarit hebdo + exceptions + congés ERPNext)
  2. Quarts à combler   — publication, candidature, approbation
  3. Pointage géolocalisé — via portail mobile, validation rayon Haversine
  4. Horaire automatique — auto-assign par disponibilité + charge + équipe
  5. Vérification congés ERPNext avant planification FSO
  6. Notifications ouverture de quarts disponibles

API whitelisted (portail + Desk) :
  get_available_technicians()  record_geo_punch()
  auto_assign_technician()     get_technician_workload()
  claim_open_shift()           get_my_schedule()
  approve_shift_claim()        get_open_shifts_for_technician()
"""
import math
import frappe
from frappe import _
from frappe.utils import (
    add_days, flt, get_datetime, getdate, now_datetime, today,
    get_first_day_of_week,
)


# ════════════════════════════════════════════════════════════════════════════
#  1. DISPONIBILITÉS TECHNICIENS
# ════════════════════════════════════════════════════════════════════════════

@frappe.whitelist()
def get_available_technicians(date, from_time=None, to_time=None,
                               fsm_location=None, fsm_team=None):
    """
    Retourne la liste des techniciens disponibles pour une date/plage donnée.
    Tient compte de :
      - Gabarit hebdomadaire FSM Technician Availability
      - Exceptions ponctuelles
      - Congés ERPNext approuvés
      - Charge hebdomadaire (ordres planifiés)
      - Filtres optionnels : lieu, équipe

    Retourne list[dict] avec clés :
      employee, employee_name, available, conflicts, workload_week
    """
    date = getdate(date)

    # Filtre de base : employés actifs Field Service User
    filters = {"status": "Active"}
    if fsm_team:
        team_members = frappe.get_all(
            "FSM Team Member",
            filters={"parent": fsm_team},
            pluck="employee",
        )
        if team_members:
            filters["name"] = ["in", team_members]

    employees = frappe.get_all(
        "Employee",
        filters=filters,
        fields=["name", "employee_name", "user_id"],
    )

    result = []
    for emp in employees:
        avail_info = _check_availability(emp.name, date, from_time, to_time)
        workload = _count_weekly_workload(emp.name, date)
        result.append({
            "employee":       emp.name,
            "employee_name":  emp.employee_name,
            "user_id":        emp.user_id,
            "available":      avail_info["available"],
            "conflicts":      avail_info["conflicts"],
            "workload_week":  workload,
        })

    result.sort(key=lambda x: (not x["available"], x["workload_week"], x["employee_name"]))
    return result


def _check_availability(employee, date, from_time=None, to_time=None):
    """Retourne {available: bool, conflicts: list[str]}."""
    conflicts = []

    # 1. Congé ERPNext
    if _has_approved_leave(employee, date):
        conflicts.append(_("Congé approuvé"))
        return {"available": False, "conflicts": conflicts}

    # 2. Fiche disponibilité FSM
    rec_name = frappe.db.get_value("FSM Technician Availability", {"employee": employee}, "name")
    if not rec_name:
        return {"available": True, "conflicts": []}

    avail = frappe.get_cached_doc("FSM Technician Availability", rec_name)
    date_str = str(date)

    # Exception ponctuelle prime sur le gabarit
    for exc in (avail.exceptions or []):
        if str(exc.exception_date) == date_str:
            if not exc.is_available:
                reason = exc.reason or _("Exception de disponibilité")
                conflicts.append(reason)
                return {"available": False, "conflicts": conflicts}
            if from_time and exc.from_time and exc.to_time:
                if not _time_overlap(from_time, to_time, exc.from_time, exc.to_time):
                    conflicts.append(_("Hors plage disponible ce jour"))
                    return {"available": False, "conflicts": conflicts}
            return {"available": True, "conflicts": []}

    # Gabarit hebdomadaire
    _DAYS = ["Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi", "Samedi", "Dimanche"]
    day_name = _DAYS[date.weekday()]
    for row in (avail.week_schedule or []):
        if row.day_of_week == day_name:
            if not row.is_available:
                conflicts.append(_("Indisponible le {0}").format(day_name))
                return {"available": False, "conflicts": conflicts}
            if from_time and row.from_time and row.to_time:
                if not _time_overlap(from_time, to_time, row.from_time, row.to_time):
                    conflicts.append(_("Hors plage disponible ({0})").format(day_name))
                    return {"available": False, "conflicts": conflicts}
            return {"available": True, "conflicts": []}

    return {"available": True, "conflicts": []}


def _has_approved_leave(employee, date):
    """True si une Leave Application approuvée couvre la date."""
    try:
        return bool(frappe.db.count(
            "Leave Application",
            filters={
                "employee": employee,
                "status": "Approved",
                "from_date": ["<=", date],
                "to_date": [">=", date],
                "docstatus": 1,
            },
        ))
    except Exception:
        return False


def _count_weekly_workload(employee, date):
    """Compte les FSO planifiés pour la semaine contenant 'date'."""
    week_start = get_first_day_of_week(date)
    week_end = add_days(week_start, 6)
    return frappe.db.count(
        "Field Service Order",
        filters={
            "assigned_to": employee,
            "scheduled_date": ["between", [str(week_start), str(week_end)]],
            "status": ["not in", ["Annulé", "Terminé", "Facturé"]],
        },
    )


def _time_overlap(req_from, req_to, avail_from, avail_to):
    if not req_to:
        return str(req_from) >= str(avail_from)
    return str(req_from) >= str(avail_from) and str(req_to) <= str(avail_to)


# ════════════════════════════════════════════════════════════════════════════
#  2. HORAIRE AUTOMATIQUE (auto-assign)
# ════════════════════════════════════════════════════════════════════════════

@frappe.whitelist()
def auto_assign_technician(fso_name):
    """
    Assigne automatiquement le meilleur technicien disponible à un FSO.
    Critères de sélection (dans l'ordre) :
      1. Disponibilité (gabarit + congés)
      2. Appartenance à l'équipe du FSO (si définie)
      3. Charge hebdomadaire minimale (équilibrage)
      4. Proximité du lieu (si lat/lng définis)

    Retourne le nom de l'employé assigné, ou None.
    """
    fso = frappe.get_doc("Field Service Order", fso_name)
    if fso.assigned_to:
        frappe.throw(_("Un technicien est déjà assigné à cet ordre."))

    candidates = get_available_technicians(
        date=fso.scheduled_date or today(),
        from_time=str(fso.scheduled_time) if fso.scheduled_time else None,
        fsm_location=fso.fsm_location,
        fsm_team=fso.fsm_team,
    )
    available = [c for c in candidates if c["available"]]
    if not available:
        frappe.msgprint(
            _("Aucun technicien disponible pour le {0}.").format(fso.scheduled_date),
            title=_("Affectation automatique"),
            indicator="orange",
        )
        return None

    # Proximité géographique si le FSO a un lieu avec coordonnées
    fso_coords = _get_location_coords(fso.fsm_location) if fso.fsm_location else None
    if fso_coords:
        for c in available:
            emp_location = frappe.db.get_value("Employee", c["employee"], "fsm_location")
            if emp_location:
                emp_coords = _get_location_coords(emp_location)
                if emp_coords:
                    c["_distance"] = _haversine(
                        fso_coords["lat"], fso_coords["lng"],
                        emp_coords["lat"], emp_coords["lng"],
                    )
                    continue
            c["_distance"] = 999_999
        available.sort(key=lambda x: (x["workload_week"], x.get("_distance", 999_999)))

    best = available[0]
    fso.assigned_to = best["employee"]
    fso.assigned_to_name = best["employee_name"]
    fso.save(ignore_permissions=True)

    frappe.msgprint(
        _("Technicien {0} assigné automatiquement.").format(best["employee_name"]),
        title=_("Affectation automatique"),
        indicator="green",
        alert=True,
    )
    return best["employee"]


# ════════════════════════════════════════════════════════════════════════════
#  3. QUARTS À COMBLER
# ════════════════════════════════════════════════════════════════════════════

@frappe.whitelist()
def get_open_shifts_for_technician():
    """
    Retourne les quarts ouverts pour lesquels le technicien connecté est
    disponible. Utilisé par le portail /my/schedule.
    """
    employee = _get_current_employee()
    if not employee:
        return []

    open_shifts = frappe.get_all(
        "FSM Open Shift",
        filters={"status": "Ouvert"},
        fields=["name", "title", "shift_date", "from_time", "to_time",
                "fsm_location", "fsm_team", "description", "headcount"],
        order_by="shift_date asc",
    )

    result = []
    for shift in open_shifts:
        avail = _check_availability(
            employee, getdate(shift.shift_date),
            str(shift.from_time) if shift.from_time else None,
            str(shift.to_time) if shift.to_time else None,
        )
        result.append({**shift, "i_am_available": avail["available"]})
    return result


@frappe.whitelist()
def claim_open_shift(shift_name):
    """
    Le technicien connecté postule pour un quart à combler.
    Ajoute une ligne dans la table claims + notifie les gestionnaires.
    """
    employee = _get_current_employee()
    if not employee:
        frappe.throw(_("Aucun profil employé associé à votre compte."))

    shift = frappe.get_doc("FSM Open Shift", shift_name)
    if shift.status != "Ouvert":
        frappe.throw(_("Ce quart n'est plus disponible."))

    # Déjà candidaté ?
    existing = [c for c in (shift.claims or []) if c.employee == employee]
    if existing:
        frappe.throw(_("Vous avez déjà postulé pour ce quart."))

    shift.append("claims", {
        "employee": employee,
        "claimed_at": now_datetime(),
        "status": "En attente",
    })
    shift.status = "Réclamé"
    shift.claimed_by = employee
    shift.claimed_at = now_datetime()
    shift.save(ignore_permissions=True)

    _notify_managers_new_claim(shift, employee)

    frappe.msgprint(
        _("Votre candidature pour «{0}» a été envoyée.").format(shift.title),
        alert=True, indicator="green",
    )
    return shift_name


@frappe.whitelist()
def approve_shift_claim(shift_name, employee):
    """
    Le gestionnaire approuve la candidature d'un technicien.
    Met à jour le statut + crée optionnellement le FSO si requis.
    """
    frappe.only_for("Field Service Manager")
    shift = frappe.get_doc("FSM Open Shift", shift_name)

    # Accepte la candidature choisie, refuse les autres
    for claim in (shift.claims or []):
        if claim.employee == employee:
            claim.status = "Accepté"
        elif claim.status == "En attente":
            claim.status = "Refusé"

    mgr_emp = frappe.db.get_value("Employee", {"user_id": frappe.session.user}, "name")
    shift.approved_by = mgr_emp
    shift.approved_at = now_datetime()
    shift.claimed_by = employee
    shift.status = "Assigné"
    shift.save(ignore_permissions=True)

    _notify_claim_result(shift, employee, accepted=True)
    frappe.msgprint(
        _("{0} assigné au quart «{1}».").format(
            frappe.db.get_value("Employee", employee, "employee_name"), shift.title
        ),
        indicator="green",
        alert=True,
    )
    return shift_name


def notify_open_shift_to_team(shift_name):
    """
    Envoie un email à tous les techniciens disponibles pour ce quart.
    Appelé depuis FSMOpenShift.on_update.
    """
    shift = frappe.get_doc("FSM Open Shift", shift_name)
    candidates = get_available_technicians(
        date=shift.shift_date,
        from_time=str(shift.from_time) if shift.from_time else None,
        to_time=str(shift.to_time) if shift.to_time else None,
        fsm_team=shift.fsm_team,
    )
    emails = []
    for c in candidates:
        if c["available"] and c.get("user_id"):
            email = frappe.db.get_value("User", c["user_id"], "email")
            if email:
                emails.append(email)

    if not emails:
        return

    subject = _("Quart à combler : {0} le {1}").format(shift.title, shift.shift_date)
    message = _(
        "<p>Un quart est disponible pour lequel vous êtes disponible :</p>"
        "<ul>"
        "<li><strong>{0}</strong></li>"
        "<li>Date : {1}</li>"
        "<li>Horaire : {2} – {3}</li>"
        "</ul>"
        "<p><a href='{4}'>Voir et postuler</a></p>"
    ).format(
        shift.title, shift.shift_date, shift.from_time, shift.to_time,
        frappe.utils.get_url("/my/schedule"),
    )
    frappe.sendmail(recipients=emails, subject=subject, message=message)


def _notify_managers_new_claim(shift, employee):
    emp_name = frappe.db.get_value("Employee", employee, "employee_name")
    managers = frappe.get_all(
        "Has Role",
        filters={"role": "Field Service Manager", "parenttype": "User"},
        pluck="parent",
    )
    emails = [frappe.db.get_value("User", u, "email") for u in managers if u != "Guest"]
    emails = [e for e in emails if e]
    if not emails:
        return
    frappe.sendmail(
        recipients=emails,
        subject=_("Candidature reçue : {0} pour «{1}»").format(emp_name, shift.title),
        message=_(
            "<p><strong>{0}</strong> a postulé pour le quart «{1}» du {2}.</p>"
            "<p><a href='{3}'>Voir le quart</a></p>"
        ).format(
            emp_name, shift.title, shift.shift_date,
            frappe.utils.get_link_to_form("FSM Open Shift", shift.name),
        ),
    )


def _notify_claim_result(shift, employee, accepted):
    user_id = frappe.db.get_value("Employee", employee, "user_id")
    if not user_id:
        return
    email = frappe.db.get_value("User", user_id, "email")
    if not email:
        return
    if accepted:
        subject = _("✅ Candidature acceptée : {0}").format(shift.title)
        msg = _("<p>Votre candidature pour le quart «{0}» du {1} a été <strong>acceptée</strong>.</p>").format(
            shift.title, shift.shift_date
        )
    else:
        subject = _("Candidature non retenue : {0}").format(shift.title)
        msg = _("<p>Votre candidature pour le quart «{0}» du {1} n'a pas été retenue.</p>").format(
            shift.title, shift.shift_date
        )
    frappe.sendmail(recipients=[email], subject=subject, message=msg)


# ════════════════════════════════════════════════════════════════════════════
#  4. POINTAGE GÉOLOCALISÉ
# ════════════════════════════════════════════════════════════════════════════

@frappe.whitelist()
def record_geo_punch(fso_name, punch_type, latitude=None, longitude=None,
                      accuracy=None, device_info=None):
    """
    Enregistre un pointage géolocalisé depuis le portail mobile.
    Crée un document FSM Geolocation Punch + met à jour les timesheets FSO.

    Retourne dict: {name, is_valid, within_radius, distance_from_site, message}
    """
    employee = _get_current_employee()
    if not employee:
        frappe.throw(_("Aucun profil employé associé à votre compte."))

    # Vérification préliminaire : technicien assigné à ce FSO ?
    lead = frappe.db.get_value("Field Service Order", fso_name, "assigned_to")
    is_secondary = frappe.db.exists(
        "FSM Order Technician", {"parent": fso_name, "employee": employee}
    )
    if lead != employee and not is_secondary:
        frappe.throw(_("Vous n'êtes pas assigné à cette intervention."))

    punch = frappe.new_doc("FSM Geolocation Punch")
    punch.employee = employee
    punch.field_service_order = fso_name
    punch.punch_type = punch_type
    punch.punch_time = now_datetime()
    punch.is_manual = 0

    if latitude and longitude:
        punch.latitude = flt(latitude, 7)
        punch.longitude = flt(longitude, 7)
    if accuracy:
        punch.accuracy_meters = flt(accuracy, 0)
    if device_info:
        punch.device_info = str(device_info)[:500]

    punch.insert(ignore_permissions=True)

    # Mise à jour automatique des heures FSO selon punch type
    _update_fso_times_from_punch(fso_name, punch_type, punch.punch_time)

    return {
        "name":              punch.name,
        "is_valid":          bool(punch.is_valid),
        "within_radius":     bool(punch.within_radius),
        "distance_from_site": punch.distance_from_site or 0,
        "message": _("Pointage enregistré") if punch.is_valid else
                   _("Pointage enregistré (hors rayon — {0} m)").format(
                       int(punch.distance_from_site or 0)
                   ),
    }


def _update_fso_times_from_punch(fso_name, punch_type, punch_time):
    """Met à jour actual_start / actual_end du FSO selon le type de pointage."""
    updates = {}
    if punch_type == "Arrivée":
        existing = frappe.db.get_value("Field Service Order", fso_name, "actual_start")
        if not existing:
            updates["actual_start"] = punch_time
            updates["status"] = "En cours"
    elif punch_type == "Départ":
        updates["actual_end"] = punch_time
    if updates:
        frappe.db.set_value("Field Service Order", fso_name, updates)


# ════════════════════════════════════════════════════════════════════════════
#  5. PLANNING TECHNICIEN (portail)
# ════════════════════════════════════════════════════════════════════════════

@frappe.whitelist()
def get_my_schedule(start_date=None, end_date=None):
    """
    Retourne le planning du technicien connecté pour une semaine.
    Inclut : FSO planifiés, quarts à combler disponibles, congés.
    """
    employee = _get_current_employee()
    if not employee:
        return {"orders": [], "open_shifts": [], "leaves": []}

    start = getdate(start_date) if start_date else getdate(get_first_day_of_week(today()))
    end   = getdate(end_date)   if end_date   else add_days(start, 6)

    orders = frappe.get_all(
        "Field Service Order",
        filters={
            "assigned_to": employee,
            "scheduled_date": ["between", [str(start), str(end)]],
            "status": ["not in", ["Annulé"]],
        },
        fields=[
            "name", "title", "status", "scheduled_date", "scheduled_time",
            "customer_name", "fsm_location", "priority", "activity_type",
        ],
        order_by="scheduled_date asc, scheduled_time asc",
    )

    # Quarts ouverts pour lesquels le technicien est disponible
    open_shifts_raw = frappe.get_all(
        "FSM Open Shift",
        filters={
            "status": "Ouvert",
            "shift_date": ["between", [str(start), str(end)]],
        },
        fields=["name", "title", "shift_date", "from_time", "to_time",
                "fsm_location", "description"],
    )
    open_shifts = []
    for s in open_shifts_raw:
        avail = _check_availability(employee, getdate(s.shift_date))
        if avail["available"]:
            open_shifts.append(s)

    # Congés ERPNext
    leaves = []
    try:
        leaves = frappe.get_all(
            "Leave Application",
            filters={
                "employee": employee,
                "status": "Approved",
                "from_date": ["<=", str(end)],
                "to_date": [">=", str(start)],
                "docstatus": 1,
            },
            fields=["name", "leave_type", "from_date", "to_date", "description"],
        )
    except Exception:
        pass

    return {
        "orders":      [dict(o) for o in orders],
        "open_shifts": [dict(s) for s in open_shifts],
        "leaves":      [dict(l) for l in leaves],
        "week_start":  str(start),
        "week_end":    str(end),
        "employee":    employee,
    }


@frappe.whitelist()
def get_technician_workload(employee, week_start=None):
    """
    Retourne la charge hebdomadaire d'un technicien (nb ordres par jour).
    Utilisé par l'horaire automatique et le planning gestionnaire.
    """
    start = getdate(week_start) if week_start else getdate(get_first_day_of_week(today()))
    end = add_days(start, 6)

    orders = frappe.get_all(
        "Field Service Order",
        filters={
            "assigned_to": employee,
            "scheduled_date": ["between", [str(start), str(end)]],
            "status": ["not in", ["Annulé"]],
        },
        fields=["scheduled_date", "name", "status", "scheduled_time", "title"],
        order_by="scheduled_date asc",
    )

    by_day = {}
    for o in orders:
        d = str(o.scheduled_date)
        by_day.setdefault(d, []).append(o)

    return {
        "employee": employee,
        "week_start": str(start),
        "week_end":   str(end),
        "by_day":     {k: [dict(v) for v in vs] for k, vs in by_day.items()},
        "total_orders": len(orders),
    }


# ════════════════════════════════════════════════════════════════════════════
#  6. VÉRIFICATION CONGÉS AVANT PLANIFICATION FSO
# ════════════════════════════════════════════════════════════════════════════

def check_leave_conflicts_for_fso(fso_name):
    """
    Vérifie si le technicien assigné est en congé à la date planifiée.
    Appelé depuis field_service_order.validate().
    Retourne list[str] des conflits, vide si aucun.
    """
    fso = frappe.get_doc("Field Service Order", fso_name)
    if not fso.assigned_to or not fso.scheduled_date:
        return []
    if _has_approved_leave(fso.assigned_to, getdate(fso.scheduled_date)):
        return [_("{0} est en congé le {1}.").format(
            fso.assigned_to_name or fso.assigned_to, fso.scheduled_date
        )]
    return []


# ════════════════════════════════════════════════════════════════════════════
#  7. JOBS PLANIFIÉS (appelés depuis tasks.py)
# ════════════════════════════════════════════════════════════════════════════

def notify_open_shifts_daily():
    """
    Chaque matin : notifie les techniciens disponibles pour les quarts
    ouverts des 7 prochains jours.
    """
    horizon = add_days(today(), 7)
    open_shifts = frappe.get_all(
        "FSM Open Shift",
        filters={"status": "Ouvert", "shift_date": ["between", [today(), str(horizon)]]},
        pluck="name",
    )
    for shift_name in open_shifts:
        try:
            notify_open_shift_to_team(shift_name)
        except Exception:
            frappe.log_error(frappe.get_traceback(), f"HR Engine: notify open shift — {shift_name}")


def auto_schedule_unassigned_fsos():
    """
    Chaque matin : tente d'assigner automatiquement les FSO sans technicien
    planifiés dans les 3 prochains jours.
    """
    horizon = add_days(today(), 3)
    unassigned = frappe.get_all(
        "Field Service Order",
        filters={
            "assigned_to": ["is", "not set"],
            "status": ["in", ["Nouveau", "Planifié"]],
            "scheduled_date": ["between", [today(), str(horizon)]],
        },
        pluck="name",
    )
    for fso_name in unassigned:
        try:
            auto_assign_technician(fso_name)
        except Exception:
            frappe.log_error(frappe.get_traceback(), f"HR Engine: auto_assign — {fso_name}")


# ════════════════════════════════════════════════════════════════════════════
#  Helpers internes
# ════════════════════════════════════════════════════════════════════════════

def _get_current_employee():
    """Retourne le nom Employee de l'utilisateur connecté, ou None."""
    return frappe.db.get_value("Employee", {"user_id": frappe.session.user}, "name")


def _get_location_coords(fsm_location):
    """Retourne {lat, lng} du FSM Location, ou None."""
    if not fsm_location:
        return None
    loc = frappe.db.get_value(
        "FSM Location", fsm_location, ["latitude", "longitude"], as_dict=True
    )
    if not loc or not loc.get("latitude") or not loc.get("longitude"):
        return None
    return {"lat": float(loc.latitude), "lng": float(loc.longitude)}


def _haversine(lat1, lon1, lat2, lon2):
    """Distance en mètres entre deux coordonnées GPS."""
    R = 6_371_000
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
