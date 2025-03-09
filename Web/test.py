from flask import Flask
from rdkit.ML.Cluster.Standardize import methods

app = Flask(__name__)

@app.route("/hello", methods=["GET", "POST"])
def hello_world():
    return "<h1>Hello, World, Fang!</h1>"

@app.route("/hi",methods=["POST"])
def hi():
    return "<h1>hi</h1>"

if __name__ == "__main__":
    app.run(debug=True)
