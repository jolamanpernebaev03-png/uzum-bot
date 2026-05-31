import os
import time
import json
from playwright.sync_api import sync_playwright

TOKEN_FILE = "/Users/mac/Documents/uzum/token.txt"
SESSION_FILE = "/Users/mac/Documents/uzum/uzum_session.json"
HEADERS_FILE = "/Users/mac/Documents/uzum/uzum_headers.json"
PAYLOAD_FILE = "/Users/mac/Documents/uzum/uzum_payload.json"

def refresh_token():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)

        if os.path.exists(SESSION_FILE):
            context = browser.new_context(storage_state=SESSION_FILE)
        else:
            context = browser.new_context()

        page = context.new_page()

        if not os.path.exists(SESSION_FILE):
            page.goto("https://uzum.uz")
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

        if token:
            with open(TOKEN_FILE, "w") as f:
                f.write(token)
            with open(HEADERS_FILE, "w") as f:
                json.dump(captured_headers, f, indent=2)
            if captured_payload:
                with open(PAYLOAD_FILE, "w") as f:
                    json.dump(captured_payload, f, indent=2)
                print("✅ Search payload captured!")
            else:
                print("⚠️ Search payload not captured — try scrolling the page")
        
        browser.close()
        return token

def get_token():
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
