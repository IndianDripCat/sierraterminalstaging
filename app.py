from flask import Flask, redirect, request, session, url_for
import requests
import os

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "change_this_secret")

ROBLOX_CLIENT_ID = os.environ.get("ROBLOX_CLIENT_ID")
ROBLOX_CLIENT_SECRET = os.environ.get("ROBLOX_CLIENT_SECRET")
REDIRECT_URI = "https://sierraterminalstaging-production.up.railway.app/roblox/oauth/callback"

@app.route("/roblox/oauth/start")
def roblox_oauth_start():
    authorize_url = (
        "https://apis.roblox.com/oauth/v1/authorize"
        "?response_type=code"
        f"&client_id={ROBLOX_CLIENT_ID}"
        f"&redirect_uri={REDIRECT_URI}"
        "&scope=openid profile"
        "&state=discord"
    )
    return redirect(authorize_url)

@app.route("/roblox/oauth/callback")
def roblox_oauth_callback():
    code = request.args.get("code")
    if not code:
        return "Missing code", 400

    # Exchange code for token
    token_url = "https://apis.roblox.com/oauth/v1/token"
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": REDIRECT_URI,
        "client_id": ROBLOX_CLIENT_ID,
        "client_secret": ROBLOX_CLIENT_SECRET,
    }
    resp = requests.post(token_url, data=data)
    if resp.status_code != 200:
        return f"Token error: {resp.text}", 400

    token_info = resp.json()
    access_token = token_info.get("access_token")

    # Fetch user info
    user_info_url = "https://apis.roblox.com/oauth/v1/userinfo"
    headers = {"Authorization": f"Bearer {access_token}"}
    user_resp = requests.get(user_info_url, headers=headers)
    if user_resp.status_code != 200:
        return f"User info error: {user_resp.text}", 400

    user_data = user_resp.json()
    # TODO: Log user_data to MongoDB

    return f"Roblox account linked: {user_data}"

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)

