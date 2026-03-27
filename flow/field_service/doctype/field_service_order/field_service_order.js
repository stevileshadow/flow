// Copyright (c) 2026, stevileshadow and contributors
// License: MIT

// ================================================================== //
//  Ligne pièces — calcul montant en temps réel                       //
// ================================================================== //
frappe.ui.form.on("Field Service Parts Line", {
	qty(frm, cdt, cdn) {
		_calc_parts_line_amount(frm, cdt, cdn);
	},
	rate(frm, cdt, cdn) {
		_calc_parts_line_amount(frm, cdt, cdn);
	},
	part_type(frm, cdt, cdn) {
		_calc_parts_line_amount(frm, cdt, cdn);
	},
});

function _calc_parts_line_amount(frm, cdt, cdn) {
	const row = locals[cdt][cdn];
	if (row.part_type === "En location") return; // calculé côté serveur
	const amount = flt(row.qty) * flt(row.rate);
	frappe.model.set_value(cdt, cdn, "amount", amount);
	frm.refresh_field("parts");
}

frappe.ui.form.on("Field Service Order", {

	// ------------------------------------------------------------------ //
	//  Chargement du formulaire                                            //
	// ------------------------------------------------------------------ //

	refresh(frm) {
		frm.set_query("project", function () {
			return {
				filters: frm.doc.customer
					? { customer: frm.doc.customer, status: ["!=", "Cancelled"] }
					: { status: ["!=", "Cancelled"] },
			};
		});

		if (!frm.is_new()) {
			_setup_email_button(frm);
		}
	},

	// ------------------------------------------------------------------ //
	//  Changements de champs                                               //
	// ------------------------------------------------------------------ //

	customer(frm) {
		if (frm.doc.project) {
			frappe.db
				.get_value("Project", frm.doc.project, "customer")
				.then(({ message }) => {
					if (message && message.customer !== frm.doc.customer) {
						frm.set_value("project", null);
					}
				});
		}
	},
});


// ================================================================== //
//  Bouton « Nouveau courrier » — auto-remplissage intelligent         //
// ================================================================== //

/**
 * Ajoute le bouton « 📧 Nouveau courrier » dans la barre d'actions.
 * Au clic : dialogue de sélection du type de courrier + historique récent,
 * puis ouverture du CommunicationComposer Frappe pré-rempli.
 */
function _setup_email_button(frm) {
	// Bouton principal dans la barre d'actions du formulaire
	frm.add_custom_button(__("📧 Nouveau courrier"), () => {
		_show_event_picker(frm);
	}, __("Courrier"));

	// Raccourci pour le rapport de clôture (visible seulement si Terminé)
	if (frm.doc.status === "Terminé") {
		frm.add_custom_button(__("📋 Envoyer le rapport de clôture"), () => {
			_open_composer(frm, "cloture");
		}, __("Courrier"));
	}
}

/**
 * Affiche le dialogue de sélection du type d'email avec l'historique récent.
 */
function _show_event_picker(frm) {
	frappe.call({
		method: "flow.field_service.email_engine.get_composer_payload",
		args: { order_name: frm.doc.name, event_type: "message_libre" },
		callback(r) {
			if (!r.message) return;
			const data = r.message;
			const events = data.available_events || [];

			// Construire les options du Select
			const select_options = events.map(e => {
				const suffix = e.available ? "" : __(" (statut incompatible)");
				return `${e.label}${suffix}`;
			});

			const d = new frappe.ui.Dialog({
				title: __("Nouveau courrier — Choisir le type"),
				size: "large",
				fields: [
					{
						fieldtype: "Select",
						fieldname: "event_label",
						label: __("Type de courrier"),
						options: select_options.join("\n"),
						default: select_options[select_options.length - 1], // message_libre
						reqd: 1,
						description: __("Le contenu sera pré-rempli automatiquement selon le type choisi."),
					},
					{
						fieldtype: "Section Break",
						label: __("Historique des courriers envoyés"),
						collapsible: 1,
					},
					{
						fieldtype: "HTML",
						fieldname: "history_html",
						options: _build_history_html(data.last_emails || []),
					},
				],
				primary_action_label: __("Préparer le courrier →"),
				primary_action(values) {
					d.hide();
					// Retrouver la valeur (key) depuis le label sélectionné
					const idx = select_options.indexOf(values.event_label);
					const event_key = (idx >= 0 && events[idx]) ? events[idx].value : "message_libre";
					_open_composer(frm, event_key);
				},
			});
			d.show();
		},
	});
}

/**
 * Récupère le payload depuis l'API Python et ouvre le CommunicationComposer
 * avec tous les champs pré-remplis (destinataires, sujet, corps, PDF).
 */
function _open_composer(frm, event_type) {
	frappe.call({
		method: "flow.field_service.email_engine.get_composer_payload",
		args: { order_name: frm.doc.name, event_type },
		freeze: true,
		freeze_message: __("Préparation du courrier…"),
		callback(r) {
			if (!r.message) return;
			const d = r.message;

			new frappe.views.CommunicationComposer({
				doc: frm.doc,
				frm: frm,
				subject: d.subject,
				recipients: d.recipients,
				cc: d.cc || "",
				message: d.message,
				attach_document_print: d.attach_pdf ? 1 : 0,
				print_format: d.print_format,
			});
		},
	});
}

/**
 * Construit le HTML de l'historique des Communications récentes.
 */
function _build_history_html(emails) {
	if (!emails || emails.length === 0) {
		return `<p class="text-muted small mt-2">${__("Aucun courrier envoyé récemment.")}</p>`;
	}

	const rows = emails.map(e => {
		const sent = e.sent_or_received === "Sent";
		const badge = sent
			? `<span class="badge bg-success">${__("Envoyé")}</span>`
			: `<span class="badge bg-secondary">${__("Reçu")}</span>`;
		const date = frappe.datetime.str_to_user(e.creation);
		const subj = frappe.utils.escape_html(e.subject || "—");
		return `<tr><td class="small">${date}</td><td class="small">${subj}</td><td>${badge}</td></tr>`;
	}).join("");

	return `
		<div class="mt-2">
			<table class="table table-sm table-bordered mb-0">
				<thead class="table-light">
					<tr>
						<th class="small">${__("Date")}</th>
						<th class="small">${__("Sujet")}</th>
						<th class="small">${__("Statut")}</th>
					</tr>
				</thead>
				<tbody>${rows}</tbody>
			</table>
		</div>`;
}
