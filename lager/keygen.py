"""Print fresh API keys:  python -m lager.keygen"""
import secrets

if __name__ == "__main__":
    print("LAGER_API_KEY=" + secrets.token_urlsafe(32))
    print("LAGER_READ_KEY=" + secrets.token_urlsafe(32))
