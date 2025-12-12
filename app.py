from flask import Flask, render_template, jsonify
import os

app = Flask(__name__)

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/get-plan')
def get_plan():
    try:
        with open('downward/sas_plan', 'r') as f:
            plan_content = f.read()
        return jsonify({"success": True, "plan": plan_content})
    except FileNotFoundError:
        return jsonify({"success": False, "error": "plan not found"}), 404

if __name__ == '__main__':
    app.run(debug=True)