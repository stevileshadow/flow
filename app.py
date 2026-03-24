from flask import Flask, render_template

app = Flask(__name__)

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/tarification")
def tarification():
    return render_template("pricing.html")

@app.route("/configurateur/standard")
def configurateur_standard():
    return render_template("configurateur.html", plan="standard", plan_label="Standard")

@app.route("/configurateur/personnalise")
def configurateur_personnalise():
    return render_template("configurateur.html", plan="custom", plan_label="Personnalisé")

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
