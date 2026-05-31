import os
import time
import json

TOKEN_FILE = "/app/token.txt" if os.path.exists("/app") else "/Users/mac/Documents/uzum/token.txt"
SESSION_FILE = "/app/uzum_session.json" if os.path.exists("/app") else "/Users/mac/Documents/uzum/uzum_session.json"
HEADERS_FILE = "/app/uzum_headers.json" if os.path.exists("/app") else "/Users/mac/Documents/uzum/uzum_headers.json"
PAYLOAD_FILE = "/app/uzum_payload.json" if os.path.exists("/app") else "/Users/mac/Documents/uzum/uzum_payload.json"
COOKIES_FILE = "/app/uzum_cookies.txt" if os.path.exists("/app") else "/Users/mac/Documents/uzum/uzum_cookies.txt"

def refresh_token():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("⚠️ Playwright not available. Using env token.")
        return os.getenv("UZUM_BEARER_TOKEN", "").strip()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        if os.path.exists(SESSION_FILE):
            context = browser.new_context(storage_state=SESSION_FILE)
        else:
            context = browser.new_context()
        page = context.new_page()
        page.goto("https://uzum.uz")
        if not os.path.exists(SESSION_FILE):
            input("👉 Login manually in browser, then press ENTER...")
            context.storage_state(path=SESSION_FILE)
        token = None
        captured_headers = {}
        captured_payload = {}
        def capture_request(request):
            nonlocal token, captured_headers, captured_payload
            if "graphql.uzum.uz" not in request.url:
                return
            headers = dict(request.headers)
            try:
                payload = request.post_data_json or {}
            except Exception:
                payload = {}
            if "authorization" in headers and "Bearer " in headers["authorization"]:
                token = headers["authorization"].replace("Bearer ", "").strip()
                captured_headers = headers
                with open(HEADERS_FILE, "w") as f:
                    json.dump(captured_headers, f, indent=2)
                with open(TOKEN_FILE, "w") as f:
                    f.write(token)
                op = payload.get("operationName", "")
                if op == "MakeSearch_ItemsAndFilters":
                    captured_payload = payload
                    with open(PAYLOAD_FILE, "w") as f:
                        json.dump(captured_payload, f, indent=2)
                    print(f"✅ Search payload saved: {op}")
        page.on("request", capture_request)
        page.goto("https://uzum.uz/ru/category/krasota-i-uhod-10012")
        time.sleep(6)
        cookies = context.cookies()
        cookie_string = "; ".join([f"{c['name']}={c['value']}" for c in cookies])
        with open(COOKIES_FILE, "w") as f:
            f.write(cookie_string)
        browser.close()
        return token

def get_token():
    env_token = os.getenv("UZUM_BEARER_TOKEN", "").strip()
    if env_token:
        return env_token
    if os.path.exists(TOKEN_FILE):
        token_age = time.time() - os.path.getmtime(TOKEN_FILE)
        if token_age < 14400:
            with open(TOKEN_FILE, "r") as f:
                return f.read().strip()
    print("🔄 Token expired, refreshing...")
    return refresh_token()

def get_headers():
    if os.path.exists(HEADERS_FILE):
        with open(HEADERS_FILE, "r") as f:
            return json.load(f)
    return {}

if __name__ == "__main__":
    token = refresh_token()
    if token:
        print(f"Token: {token[:50]}...")
