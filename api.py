from flask import Flask, jsonify, request

app = Flask(__name__)

# This is the root URL. Vercel needs this to verify the app is running.
@app.route('/')
def home():
    return jsonify({
        "status": "success",
        "message": "Transport Agent API is running!"
    })

# This is an example API endpoint. You can add more later.
@app.route('/api/calculate', methods=['POST'])
def calculate():
    data = request.json
    # TODO: Replace this with your actual logic from app.py
    # e.g., result = your_function(data['distance'])
    return jsonify({
        "received": data,
        "result": "Your logic goes here"
    })

if __name__ == '__main__':
    app.run(port=5000)
