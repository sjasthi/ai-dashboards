from flask import Flask, request, render_template, jsonify
import pandas as pd
import requests

app = Flask(__name__)

def get_ai_recommendations(df):
    url = "https://api.anthropic.com/v1/queries"
    headers = {"Content-Type": "application/json", "Authorization": "Bearer YOUR_API_KEY"}
    payload = {
        "model": "claude-2",
        "prompt": f"Analyze the following dataset and provide 3 recommendations: {df.to_json()}",
        "max_tokens_to_sample": 500
    }
    response = requests.post(url, headers=headers, json=payload)
    return response.json()

def analyze_data(df):
    summary = df.describe().T
    return summary

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/upload', methods=['POST'])
def upload_file():
    file = request.files['file']
    if file:
        df = pd.read_csv(file)
        summary = analyze_data(df)
        ai_recommendations = get_ai_recommendations(df)
        return jsonify(summary=summary.to_dict(), recommendations=ai_recommendations)
    return "No file uploaded"

if __name__ == '__main__':
    app.run(debug=True)
