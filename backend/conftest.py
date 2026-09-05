import os, sys
sys.path.insert(0, os.path.dirname(__file__))

# Set placeholder Schwab credentials so tests can collect without real credentials.
# setdefault ensures a real .env or exported vars still take precedence.
os.environ.setdefault("SCHWAB_APP_KEY", "test-app-key")
os.environ.setdefault("SCHWAB_APP_SECRET", "test-app-secret")
