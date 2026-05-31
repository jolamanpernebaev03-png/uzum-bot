import os
import json
import requests
from dotenv import load_dotenv

load_dotenv("/Users/mac/Documents/uzum/.env")

RAILWAY_API_TOKEN = os.getenv("RAILWAY_API_TOKEN", "")
RAILWAY_SERVICE_ID = os.getenv("RAILWAY_SERVICE_ID", "")
RAILWAY_ENVIRONMENT_ID = os.getenv("RAILWAY_ENVIRONMENT_ID", "")

def push_token_to_railway(token):
    query = """
    mutation UpsertVariables($input: VariableCollectionUpsertInput!) {
        variableCollectionUpsert(input: $input)
    }
    """
    variables = {
        "input": {
            "projectId": "397b4b24-1ecf-4888-a321-f7f994cd8e16",
            "environmentId": RAILWAY_ENVIRONMENT_ID,
            "serviceId": RAILWAY_SERVICE_ID,
            "variables": {
                "UZUM_BEARER_TOKEN": token
            }
        }
    }
    for attempt in range(3):
        try:
            resp = requests.post(
                "https://backboard.railway.app/graphql/v2",
                headers={
                    "Authorization": f"Bearer {RAILWAY_API_TOKEN}",
                    "Content-Type": "application/json",
                },
                json={"query": query, "variables": variables},
                timeout=30,
            )
            if resp.status_code == 200 and not resp.json().get("errors"):
                print("✅ Token pushed to Railway successfully")
                return True
            else:
                print(f"❌ Railway push failed: {resp.text[:200]}")
                return False
        except requests.exceptions.Timeout:
            print(f"⏱️ Attempt {attempt+1}/3 timed out, retrying...")
        except Exception as e:
            print(f"❌ Attempt {attempt+1}/3 error: {e}")
            if attempt == 2:
                return False
    return False

def refresh_and_push():
    print("🔄 Refreshing Uzum token...")
    from uzum_token_manager import get_token
    token = get_token()
    if not token:
        print("❌ Could not get token")
        return False
    print(f"✅ Got token: {token[:30]}...")
    return push_token_to_railway(token)

if __name__ == "__main__":
    refresh_and_push()
