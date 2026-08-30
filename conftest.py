import os
import sys

# Ensure root path is in sys.path
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

# Provide mock environment variables for test imports
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "123456789:FAKE_TOKEN_FOR_TESTING")
os.environ.setdefault("GEMINI_API_KEY", "FAKE_KEY_FOR_TESTING")
