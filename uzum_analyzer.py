import json
import os
import time
from datetime import datetime

import requests
from dotenv import load_dotenv
load_dotenv("/Users/mac/Documents/uzum/.env")

# ============================================================
# CONFIGURATION
# ============================================================
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "687396965")
from uzum_token_manager import get_token as _get_uzum_token
UZUM_BEARER_TOKEN = os.getenv("UZUM_BEARER_TOKEN", "").strip() or _get_uzum_token()
UZUM_COOKIE = os.getenv("UZUM_COOKIE", "")

UZS_TO_USD = float(os.getenv("UZS_TO_USD", "12700"))

# All major Uzum categories with their IDs
CATEGORIES = {
    "Красота и уход":       10012,
    "Корейская косметика":  74,
    "Уход за лицом":        10016,
    "Маникюр и педикюр":   10014,
    "Уход за волосами":     10015,
    "Канцтовары":           10010,
    "Детские товары":       10006,
    "Зоотовары":            10009,
    "Спорт и отдых":        10021,
    "Электроника":          10004,
    "Одежда":               10018,
    "Обувь":                10019,
    "Здоровье":             10008,
    "Бытовая химия":        10003,
    "Продукты питания":     10020,
    "Мебель":               10011,
    "Дача и сад":           10005,
    "Автотовары":           10001,
}

# Seasonal calendar for Uzbekistan
SEASONAL_CALENDAR = {
    1:  ["зима", "новый год", "подарки", "согревание", "увлажнение кожи"],
    2:  ["зима", "день влюбленных", "подарки", "уход за кожей"],
    3:  ["весна", "навруз", "подарки", "уборка", "садоводство"],
    4:  ["весна", "рамадан", "садоводство", "спорт на улице"],
    5:  ["весна", "курбан хайит", "летняя одежда", "спорт"],
    6:  ["лето", "жара", "солнцезащитный крем", "охлаждение", "легкая одежда"],
    7:  ["лето", "жара", "пляж", "охлаждение", "напитки"],
    8:  ["лето", "школа", "канцтовары", "рюкзаки", "форма"],
    9:  ["осень", "школа", "канцтовары", "увлажнение"],
    10: ["осень", "уход за кожей", "теплая одежда", "витамины"],
    11: ["осень", "зима", "теплая одежда", "отопление"],
    12: ["зима", "новый год", "подарки", "праздники", "декор"],
}

import json as _json

def _load_real_headers():
    headers_file = "/Users/mac/Documents/uzum/uzum_headers.json"
    token_file = "/Users/mac/Documents/uzum/token.txt"
    if os.path.exists(headers_file):
        with open(headers_file, "r") as f:
            h = _json.load(f)
        if os.path.exists(token_file):
            with open(token_file, "r") as f:
                token = f.read().strip()
            h["authorization"] = f"Bearer {token}"
        return h
    return {}

HEADERS = _load_real_headers()

def _load_real_query():
    if os.path.exists("/Users/mac/Documents/uzum/uzum_payload.json"):
        with open("/Users/mac/Documents/uzum/uzum_payload.json", "r") as f:
            return json.load(f).get("query", "")
    return ""

PRODUCT_QUERY = _load_real_query()

SESSION = requests.Session()

# ============================================================
# SCRAPING
# ============================================================

def post_with_retry(url, headers, payload, timeout=20, attempts=3, pause=1.2):
    last_error = None
    for attempt in range(1, attempts + 1):
        try:
            response = SESSION.post(url, headers=headers, json=payload, timeout=timeout)
            if response.status_code < 500:
                return response
            last_error = f"HTTP {response.status_code}: {response.text[:200]}"
        except Exception as e:
            last_error = str(e)
        if attempt < attempts:
            time.sleep(pause * attempt)
    raise RuntimeError(last_error or "Unknown request failure")


def scrape_category(category_name, category_id, offset=0, limit=48):
    query_input = {
        "categoryId": str(category_id),
        "showAdultContent": "NONE",
        "filters": [],
        "sort": "BY_RELEVANCE_DESC",
        "pagination": {"offset": int(offset), "limit": int(limit)},
        "correctQuery": False,
        "getFastCategories": True,
        "fastCategoriesLimit": 11,
        "fastCategoriesLevelOffset": 2,
        "getPromotionItems": True,
        "getFastFacets": False,
        "fastFacetsLimit": 0,
    }
    payload = {
        "operationName": "MakeSearch_ItemsAndFilters",
        "query": PRODUCT_QUERY,
        "variables": {"queryInput": query_input},
    }
    try:
        response = post_with_retry("https://graphql.uzum.uz/", HEADERS, payload, timeout=20, attempts=3)
        if response.status_code == 200:
            data = response.json()
            search = data.get("data", {}).get("makeSearch", {})
            items = search.get("items", [])
            total = search.get("total", 0)
            products = []
            for item in items:
                card = item.get("catalogCard", {})
                if not card:
                    continue
                price = card.get("minSellPrice", 0) or 0
                full_price = card.get("minFullPrice", 0) or price
                discount = round(((full_price - price) / full_price * 100), 1) if full_price > price else 0
                reviews = card.get("feedbackQuantity", 0) or 0
                product_id = card.get("productId") or card.get("id", "")
                products.append({
                    "id": product_id,
                    "name": card.get("title", "Unknown"),
                    "price_uzs": price,
                    "price_usd": round(price / UZS_TO_USD, 2),
                    "original_price_uzs": full_price,
                    "discount_pct": discount,
                    "rating": card.get("rating", 0) or 0,
                    "reviews": reviews,
                    "orders": 0,
                    "category": category_name,
                    "category_id": category_id,
                    "url": f"https://uzum.uz/ru/product/{product_id}",
                    "scraped_at": datetime.now().isoformat(),
                })
            return products, total
        else:
            print(f"  HTTP {response.status_code} for {category_name}: {response.text[:200]}")
            return [], 0
    except Exception as e:
        print(f"  Error scraping {category_name}: {e}")
        return [], 0


def scrape_all_categories(pages_per_category=3):
    """Scrape all categories"""
    all_products = []

    for cat_name, cat_id in CATEGORIES.items():
        print(f"\n📦 {cat_name} (ID: {cat_id})")
        category_products = []

        for page in range(pages_per_category):
            offset = page * 48
            products, total = scrape_category(cat_name, cat_id, offset=offset)
            category_products.extend(products)

            if page == 0:
                print(f"   Total available: {total}")

            print(f"   Page {page+1}: +{len(products)} products")

            if len(category_products) >= total or not products:
                break

            time.sleep(1.5)

        all_products.extend(category_products)
        print(f"   ✅ {len(category_products)} products collected")
        time.sleep(2)

    return all_products


# ============================================================
# SCORING
# ============================================================

def score_product(product):
    """Score opportunity 0-100"""
    score = 0
    flags = []

    # Demand score (reviews)
    reviews = product.get("reviews", 0)
    if reviews >= 2000:
        score += 25; flags.append("🔥 Huge demand")
    elif reviews >= 500:
        score += 18; flags.append("📈 High demand")
    elif reviews >= 100:
        score += 10; flags.append("👍 Moderate demand")
    elif reviews >= 10:
        score += 5; flags.append("🌱 Growing")

    # Velocity score (orders or proxy)
    orders = product.get("orders", 0)
    if orders >= 10000:
        score += 25; flags.append("🚀 Best seller")
    elif orders >= 3000:
        score += 18; flags.append("⚡ Fast moving")
    elif orders >= 500:
        score += 10; flags.append("📦 Good velocity")
    elif orders >= 50:
        score += 5

    # Rating quality
    rating = product.get("rating", 0)
    if rating >= 4.8:
        score += 15; flags.append("⭐ Top rated")
    elif rating >= 4.5:
        score += 10; flags.append("✅ Well rated")
    elif rating >= 4.0:
        score += 5

    # Price opportunity (sweet spot for importing)
    price_usd = product.get("price_usd", 0)
    if 3 <= price_usd <= 15:
        score += 15; flags.append("💰 Great price point")
    elif 15 < price_usd <= 50:
        score += 10; flags.append("💵 Good margin potential")
    elif price_usd > 50:
        score += 5

    # Discount signal (means original price was higher = premium product)
    discount = product.get("discount_pct", 0)
    if discount >= 20:
        score += 10; flags.append("🏷️ Premium product on sale")
    elif discount >= 10:
        score += 5

    # Seasonal bonus
    current_month = datetime.now().month
    seasonal_keywords = SEASONAL_CALENDAR.get(current_month, [])
    product_name_lower = product.get("name", "").lower()
    for keyword in seasonal_keywords:
        if keyword in product_name_lower:
            score += 10
            flags.append(f"🗓️ In season now")
            break

    product["score"] = min(score, 100)
    product["flags"] = " | ".join(flags) if flags else "No signals"
    return product


def get_seasonal_prediction(products):
    """Predict what will be hot next month"""
    next_month = (datetime.now().month % 12) + 1
    next_keywords = SEASONAL_CALENDAR.get(next_month, [])

    predicted = []
    for p in products:
        name_lower = p.get("name", "").lower()
        for kw in next_keywords:
            if kw in name_lower:
                predicted.append(p)
                break

    return predicted, next_keywords


# ============================================================
# CLAUDE ANALYSIS
# ============================================================

def analyze_with_claude(top_products, seasonal_products, next_keywords):
    """Deep AI analysis"""
    print("\n🤖 Running Claude analysis...")

    current_month_name = datetime.now().strftime("%B")
    next_month = datetime.now().month % 12 + 1
    next_month_name = ["", "January", "February", "March", "April", "May",
                       "June", "July", "August", "September", "October",
                       "November", "December"][next_month]

    # Format top products
    top_text = ""
    for i, p in enumerate(top_products[:15], 1):
        top_text += f"\n{i}. {p['name'][:60]}"
        top_text += f"\n   💰 ${p['price_usd']} | ⭐{p['rating']} | 📝{p['reviews']} reviews"
        top_text += f"\n   📂 {p['category']} | 🎯 Score: {p['score']}/100"
        top_text += f"\n   {p['flags']}\n"

    # Format seasonal products
    seasonal_text = ""
    for i, p in enumerate(seasonal_products[:5], 1):
        seasonal_text += f"\n{i}. {p['name'][:60]} — ${p['price_usd']}"

    prompt = f"""You are a sharp e-commerce business analyst helping find profitable products to import and sell on Uzum.uz (Uzbekistan's largest marketplace).

Today: {datetime.now().strftime('%B %d, %Y')}
Current season context: {', '.join(SEASONAL_CALENDAR.get(datetime.now().month, []))}
Next month ({next_month_name}) keywords: {', '.join(next_keywords)}

=== TOP SCORING PRODUCTS TODAY ===
{top_text}

=== PRODUCTS PREDICTED HOT NEXT MONTH ===
{seasonal_text if seasonal_text else "None matched seasonal keywords"}

Please provide:

🏆 TOP 5 PRODUCTS TO SELL NOW
For each: why it's an opportunity, where to source (Korea/China/Kazakhstan), estimated source price, expected margin %

📅 TOP 3 PRODUCTS TO STOCK NOW FOR NEXT MONTH
Explain the seasonal angle and timing strategy

⚠️ RED FLAGS
Any products to avoid and why

💡 MARKET INSIGHT
Key trends you see in this Uzbekistan market data

🎯 ACTION PLAN
Concrete next steps for someone starting with $2000-5000 budget

Be specific, practical, numbers-focused. Format for Telegram with emojis."""

    try:
        response = post_with_retry(
            "https://api.anthropic.com/v1/messages",
            {
                "x-api-key": ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
                "Content-Type": "application/json",
            },
            {
                "model": "claude-sonnet-4-5",
                "max_tokens": 2000,
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=60,
            attempts=2,
        )

        if response.status_code == 200:
            return response.json()["content"][0]["text"]
        else:
            return f"Claude error: {response.status_code} — {response.text}"

    except Exception as e:
        return f"Claude error: {e}"


# ============================================================
# TELEGRAM
# ============================================================

def send_telegram(message, parse_mode="Markdown"):
    """Send message to Telegram in chunks"""
    if not TELEGRAM_BOT_TOKEN:
        print("Telegram skipped: TELEGRAM_BOT_TOKEN is not set.")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    chunks = [message[i:i+4000] for i in range(0, len(message), 4000)]

    for chunk in chunks:
        try:
            requests.post(
                url,
                json={
                    "chat_id": TELEGRAM_CHAT_ID,
                    "text": chunk,
                    "parse_mode": parse_mode,
                },
                timeout=10,
            )
            time.sleep(0.5)
        except Exception as e:
            print(f"Telegram error: {e}")


def build_top_products_message(top_products, total_scraped):
    """Build formatted Telegram message"""
    now = datetime.now().strftime("%d %b %Y, %H:%M")
    msg = f"🛍 *UZUM MARKET INTELLIGENCE REPORT*\n"
    msg += f"_{now}_\n\n"
    msg += f"📊 Products analyzed: *{total_scraped}*\n"
    msg += f"📂 Categories: *{len(CATEGORIES)}*\n\n"
    msg += f"━━━━━━━━━━━━━━━━━━━\n"
    msg += f"🏆 *TOP 10 OPPORTUNITIES*\n"
    msg += f"━━━━━━━━━━━━━━━━━━━\n\n"

    for i, p in enumerate(top_products[:10], 1):
        msg += f"*{i}. {p['name'][:45]}*\n"
        msg += f"💰 ${p['price_usd']} ({p['price_uzs']:,} UZS)\n"
        msg += f"⭐ {p['rating']} | 📝 {p['reviews']} reviews | 📦 {p['orders']} orders\n"
        msg += f"📂 {p['category']}\n"
        msg += f"🎯 Score: {p['score']}/100\n"
        msg += f"{p['flags']}\n\n"

    return msg


# ============================================================
# SAVE RESULTS
# ============================================================

def save_results(all_products, top_products, analysis, seasonal_products):
    """Save full report as JSON"""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M")
    filename = f"uzum_report_{timestamp}.json"

    report = {
        "generated_at": datetime.now().isoformat(),
        "summary": {
            "total_products": len(all_products),
            "categories_analyzed": len(CATEGORIES),
            "top_score": top_products[0]["score"] if top_products else 0,
        },
        "top_opportunities": top_products[:20],
        "seasonal_predictions": seasonal_products[:10],
        "claude_analysis": analysis,
        "all_products": all_products,
    }

    with open(filename, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    print(f"\n💾 Saved: {filename}")
    return filename


# ============================================================
# MAIN
# ============================================================

def main():
    if not UZUM_BEARER_TOKEN and not UZUM_COOKIE:
        print("⚠️ Warning: UZUM_BEARER_TOKEN / UZUM_COOKIE not set. Public data requests may fail.")
    if not ANTHROPIC_API_KEY:
        print("⚠️ Warning: ANTHROPIC_API_KEY not set. Claude analysis will be skipped.")

    print("=" * 55)
    print("🚀 UZUM MARKET INTELLIGENCE SYSTEM")
    print(f"⏰ {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 55)

    # 1. Scrape all categories
    print("\n📡 SCRAPING UZUM...")
    all_products = scrape_all_categories(pages_per_category=3)
    print(f"\n✅ Total products scraped: {len(all_products)}")

    if not all_products:
        print("❌ No products found. Check internet connection.")
        send_telegram("❌ Uzum scraper failed — no products found. Check connection.")
        return

    # 2. Score all products
    print("\n📊 Scoring opportunities...")
    scored = [score_product(p) for p in all_products]
    top_products = sorted(scored, key=lambda x: x["score"], reverse=True)

    # 3. Seasonal prediction
    print("🗓️ Generating seasonal predictions...")
    seasonal_products, next_keywords = get_seasonal_prediction(scored)
    seasonal_sorted = sorted(seasonal_products, key=lambda x: x["score"], reverse=True)

    # 4. Print top 10
    print("\n🏆 TOP 10 OPPORTUNITIES:")
    print("-" * 55)
    for i, p in enumerate(top_products[:10], 1):
        print(f"{i:2}. [{p['score']:3}/100] {p['name'][:40]:<40} ${p['price_usd']:6.2f} | {p['reviews']:4} reviews | {p['category']}")

    # 5. Claude analysis
    if ANTHROPIC_API_KEY:
        analysis = analyze_with_claude(top_products, seasonal_sorted, next_keywords)
    else:
        analysis = "Claude analysis skipped: ANTHROPIC_API_KEY is missing."
    print("\n🤖 CLAUDE ANALYSIS:")
    print("-" * 55)
    print(analysis)

    # 6. Save
    save_results(all_products, top_products, analysis, seasonal_sorted)

    # 7. Send to Telegram
    print("\n📱 Sending to Telegram...")
    top_msg = build_top_products_message(top_products, len(all_products))
    send_telegram(top_msg)
    if ANTHROPIC_API_KEY:
        send_telegram(f"🤖 *CLAUDE AI ANALYSIS:*\n\n{analysis}")

    next_month = datetime.now().month % 12 + 1
    month_names = ["", "January", "February", "March", "April", "May",
                   "June", "July", "August", "September", "October", "November", "December"]

    if seasonal_sorted:
        seasonal_msg = f"📅 *STOCK NOW FOR {month_names[next_month].upper()}:*\n\n"
        for i, p in enumerate(seasonal_sorted[:5], 1):
            seasonal_msg += f"*{i}. {p['name'][:50]}*\n"
            seasonal_msg += f"💰 ${p['price_usd']} | 📝 {p['reviews']} reviews | 🎯 {p['score']}/100\n\n"
        send_telegram(seasonal_msg)

    print("\n✅ DONE! Check your Telegram for the full report.")
    print("=" * 55)


if __name__ == "__main__":
    main()
