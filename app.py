from flask import Flask, render_template_string

app = Flask(__name__)

HTML = """
<!DOCTYPE html>
<html lang="fr">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Flow - Gestion de flotte</title>
    <style>
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: #f0f4f8;
            min-height: 100vh;
            display: flex;
            align-items: center;
            justify-content: center;
            padding: 2rem;
        }
        .card {
            background: white;
            border-radius: 12px;
            box-shadow: 0 4px 24px rgba(0,0,0,0.10);
            max-width: 600px;
            width: 100%;
            padding: 3rem 2.5rem;
            text-align: center;
        }
        .icon {
            font-size: 3rem;
            margin-bottom: 1rem;
        }
        h1 {
            font-size: 2.2rem;
            color: #1a237e;
            margin-bottom: 0.5rem;
        }
        .subtitle {
            color: #5c6bc0;
            font-size: 1.1rem;
            margin-bottom: 2rem;
        }
        .badge {
            display: inline-block;
            background: #e8eaf6;
            color: #3949ab;
            border-radius: 20px;
            padding: 0.3rem 1rem;
            font-size: 0.85rem;
            font-weight: 600;
            margin-bottom: 2rem;
        }
        .info-grid {
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 1rem;
            margin-bottom: 2rem;
        }
        .info-item {
            background: #f5f7ff;
            border-radius: 8px;
            padding: 1rem;
            text-align: left;
        }
        .info-label {
            font-size: 0.75rem;
            color: #9fa8da;
            text-transform: uppercase;
            letter-spacing: 0.05em;
            margin-bottom: 0.25rem;
        }
        .info-value {
            font-size: 0.95rem;
            color: #283593;
            font-weight: 600;
        }
        .description {
            background: #e8f5e9;
            border-left: 4px solid #43a047;
            border-radius: 0 8px 8px 0;
            padding: 1rem 1.25rem;
            text-align: left;
            color: #2e7d32;
            font-size: 0.95rem;
            margin-bottom: 2rem;
            line-height: 1.6;
        }
        .note {
            background: #fff8e1;
            border-radius: 8px;
            padding: 1rem 1.25rem;
            color: #f57f17;
            font-size: 0.88rem;
            text-align: left;
            line-height: 1.6;
        }
        .note strong { color: #e65100; }
    </style>
</head>
<body>
    <div class="card">
        <div class="icon">⚡</div>
        <h1>Flow</h1>
        <p class="subtitle">Gestion de flotte et mandats de signalisation</p>
        <span class="badge">Application Frappe / ERPNext</span>

        <div class="info-grid">
            <div class="info-item">
                <div class="info-label">Nom de l'app</div>
                <div class="info-value">flow</div>
            </div>
            <div class="info-item">
                <div class="info-label">Éditeur</div>
                <div class="info-value">stevileshadow</div>
            </div>
            <div class="info-item">
                <div class="info-label">Licence</div>
                <div class="info-value">MIT</div>
            </div>
            <div class="info-item">
                <div class="info-label">Couleur</div>
                <div class="info-value">Blue</div>
            </div>
        </div>

        <div class="description">
            <strong>Description :</strong> Cette application Frappe permet la gestion de flotte de véhicules ainsi que la création et le suivi de mandats de signalisation au sein d'un environnement ERPNext.
        </div>

        <div class="note">
            <strong>Note :</strong> Flow est un module personnalisé conçu pour être installé dans une instance <strong>Frappe / ERPNext</strong>. Pour l'utiliser pleinement, installez-le via <code>bench get-app</code> dans votre environnement Frappe.
        </div>
    </div>
</body>
</html>
"""

@app.route("/")
def index():
    return render_template_string(HTML)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
