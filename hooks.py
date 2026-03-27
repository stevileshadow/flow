app_name = "flow"
app_title = "Flow"
app_publisher = "stevileshadow"
app_description = "ERP open source concurrent d'Odoo, bâti sur Frappe"
app_icon = "octicon octicon-zap"
app_color = "blue"
app_email = "ton-courriel@exemple.com"
app_license = "mit"

# Pages web servies depuis flow/www/
# Frappe détecte automatiquement le dossier www de chaque app installée

# ------------------------------------------------------------------ #
#  Portail client                                                      #
# ------------------------------------------------------------------ #

portal_menu_items = [
	{
		"title": "Mes interventions",
		"route": "/my/interventions",
		"reference_doctype": "Field Service Order",
		"role": "Customer",
	},
	{
		"title": "Mon espace technicien",
		"route": "/my/technician",
		"reference_doctype": "Field Service Order",
		"role": "Field Service User",
	},
	{
		"title": "Décompte mensuel",
		"route": "/my/decompte",
		"reference_doctype": "Field Service Order",
		"role": "Field Service Manager",
	},
]

website_route_rules = [
	{"from_route": "/my/interventions/<name>", "to_route": "my/interventions/detail"},
	{"from_route": "/my/technician/<name>", "to_route": "my/technician/detail"},
	{"from_route": "/my/signature", "to_route": "my/signature"},
	{"from_route": "/my/decompte", "to_route": "my/decompte"},
]

# ------------------------------------------------------------------ #
#  Rôles                                                              #
# ------------------------------------------------------------------ #

has_permission = {
	"Field Service Order": "flow.field_service.doctype.field_service_order.field_service_order.has_permission",
	"FSM Technician Timesheet": "flow.field_service.doctype.field_service_order.field_service_order.has_permission_timesheet",
}

# ------------------------------------------------------------------ #
#  Scheduler — rappels + vérification SLA                             #
# ------------------------------------------------------------------ #

scheduler_events = {
	"cron": {
		# Chaque matin à 7h : rappels techniciens, notifs pré-visite clients, ordres PM
		"0 7 * * *": [
			"flow.field_service.tasks.send_daily_reminders",
			"flow.field_service.tasks.send_previsit_notifications",
			"flow.field_service.tasks.generate_preventive_maintenance_orders",
		],
		# Chaque heure : retards, SLA, escalades
		"0 * * * *": [
			"flow.field_service.tasks.flag_overdue_orders",
			"flow.field_service.tasks.update_sla_statuses",
			"flow.field_service.tasks.escalate_breached_sla_orders",
		],
		# Le 1er de chaque mois à 6h : décompte mensuel envoyé aux responsables
		"0 6 1 * *": [
			"flow.field_service.tasks.send_monthly_decompte",
		],
	}
}

# ------------------------------------------------------------------ #
#  Doc Events                                                         #
# ------------------------------------------------------------------ #

doc_events = {
	"Field Service Order": {
		"on_submit": "flow.field_service.tasks.notify_technician_on_assignment",
		"on_cancel": "flow.field_service.tasks.notify_cancellation",
		"on_update": "flow.field_service.tasks.notify_customer_on_status_change",
	}
}

# ------------------------------------------------------------------ #
#  Fixtures — données de référence installées avec l'app              #
# ------------------------------------------------------------------ #

fixtures = [
	{
		"doctype": "Field Service Activity Type",
		"filters": []
	},
	{
		"doctype": "FSM Stage",
		"filters": []
	},
	{
		"doctype": "Role",
		"filters": [["role_name", "in", ["Field Service Manager", "Field Service User"]]]
	},
	{
		"doctype": "Print Format",
		"filters": [["doc_type", "=", "Field Service Order"]]
	},
]
