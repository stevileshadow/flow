# Copyright (c) 2026, stevileshadow and contributors
# License: MIT
"""Pointage géolocalisé — enregistré depuis le portail mobile technicien."""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import now_datetime


class FSMGeolocationPunch(Document):

    def validate(self):
        if not self.punch_time:
            self.punch_time = now_datetime()
        if not self.is_manual:
            self._calculate_distance()

    def _calculate_distance(self):
        """Calcule la distance entre le pointage GPS et le site de l'intervention."""
        if not (self.latitude and self.longitude and self.field_service_order):
            self.is_valid = 0
            self.within_radius = 0
            return

        location_coords = _get_fso_location_coords(self.field_service_order)
        if not location_coords:
            # Pas de coordonnées sur le lieu → pointage accepté par défaut
            self.is_valid = 1
            self.within_radius = 1
            self.distance_from_site = 0
            return

        dist = _haversine(
            self.latitude, self.longitude,
            location_coords["lat"], location_coords["lng"],
        )
        self.distance_from_site = round(dist)
        radius = location_coords.get("checkin_radius", 500)
        self.within_radius = 1 if dist <= radius else 0
        self.is_valid = self.within_radius

        if not self.within_radius:
            frappe.msgprint(
                _("Attention : vous êtes à {0} m du site (rayon autorisé : {1} m). "
                  "Le pointage est enregistré mais marqué hors rayon.").format(
                    int(dist), int(radius)
                ),
                alert=True,
                indicator="orange",
            )


# ── Helpers géo ───────────────────────────────────────────────────────────────

def _get_fso_location_coords(fso_name):
    """Retourne {lat, lng, checkin_radius} du lieu de l'intervention, ou None."""
    loc_name = frappe.db.get_value("Field Service Order", fso_name, "fsm_location")
    if not loc_name:
        return None
    loc = frappe.db.get_value(
        "FSM Location",
        loc_name,
        ["latitude", "longitude", "checkin_radius"],
        as_dict=True,
    )
    if not loc or not loc.get("latitude") or not loc.get("longitude"):
        return None
    return {
        "lat": float(loc.latitude),
        "lng": float(loc.longitude),
        "checkin_radius": float(loc.checkin_radius or 500),
    }


def _haversine(lat1, lon1, lat2, lon2):
    """Distance en mètres entre deux coordonnées (formule Haversine)."""
    import math
    R = 6_371_000  # rayon terrestre en mètres
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
