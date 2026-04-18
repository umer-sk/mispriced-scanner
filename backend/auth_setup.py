"""
Run this script ONCE locally to generate token.json.
It opens a browser for Schwab login and saves the OAuth token.

Usage:
    pip install schwab-py python-dotenv
    python auth_setup.py

After running:
    - Upload the generated token.json to Render Secret Files
    - Never commit token.json to git
"""
import os

from dotenv import load_dotenv, find_dotenv
import schwab

# Search current dir and parent dirs for .env (works when running from backend/)
load_dotenv(find_dotenv(usecwd=True) or find_dotenv())

api_key = os.environ["SCHWAB_APP_KEY"]
app_secret = os.environ["SCHWAB_APP_SECRET"]
# Port is required — schwab-py starts a local redirect server on this port
callback_url = os.environ.get("SCHWAB_CALLBACK_URL", "https://127.0.0.1:8182")
token_path = os.environ.get("SCHWAB_TOKEN_PATH", "./token.json")

print(f"Generating token at: {token_path}")
print("A browser window will open. Log in with your Schwab brokerage credentials.")
print("After login, copy the redirect URL from the browser address bar and paste it here.")

schwab.auth.easy_client(
    api_key=api_key,
    app_secret=app_secret,
    callback_url=callback_url,
    token_path=token_path,
)

print(f"\n✓ token.json created successfully at: {token_path}")
print("Next steps:")
print("  1. Upload this file to Render Secret Files as /etc/secrets/token.json")
print("  2. Set SCHWAB_TOKEN_PATH=/etc/secrets/token.json in Render env vars")
print("  3. Refresh token expires every 7 days — repeat this process weekly")
