"""
Uzum Market Intelligence — Telegram Bot
========================================
Commands:
  /start   — Welcome + ask budget → full sourcing report
  /report  — Re-run analysis with saved budget
  /top     — Quick top 5 opportunities (fast, no AI)
  /budget <amount> — Update your budget
  /status  — Bot health check
  /help    — Show all commands

Free chat:
  Ask anything about sourcing, products, profits, seasons, etc.
  DeepSeek AI answers using live Uzum market data.
"""

import asyncio
import glob
import json
import logging
import os
import time
from datetime import datetime

import requests
from telegram import Update, BotCommand
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# ── Budget persistence ──────────────────────────────────────────────────────

BUDGET_FILE = "/app/budget.txt"


def read_budget():
    if os.path.exists(BUDGET_FILE):
        with open(BUDGET_FILE, "r") as f:
            try:
                return float(f.read().strip())
            except:
                pass
    return 1000.0


def save_budget(amount):
    with open(BUDGET_FILE, "w") as f:
        f.write(str(amount))


# ── Import the data engine ──────────────────────────────────────────────────
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from uzum_analyzer import (
    scrape_all_categories,
    scrape_category,
    score_product,
    get_seasonal_prediction,
    post_with_retry,
    CATEGORIES,
    SEASONAL_CALENDAR,
    TELEGRAM_BOT_TOKEN,
    UZS_TO_USD,
)

# ── Logging ─────────────────────────────────────────────────────────────────
logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ── Helpers ──────────────────────────────────────────────────────────────────

def _deepseek(prompt: str, max_tokens: int = 1500) -> str:
    """Call DeepSeek API and return text."""
    api_key = os.getenv("DEEPSEEK_API_KEY", "")
    if not api_key:
        return "⚠️ DEEPSEEK_API_KEY is not set."
    try:
        resp = post_with_retry(
            "https://api.deepseek.com/v1/chat/completions",
            {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            {
                "model": "deepseek-chat",
                "max_tokens": max_tokens,
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=40,
            attempts=2,
        )
        if resp.status_code == 200:
            return resp.json()["choices"][0]["message"]["content"]
        return f"DeepSeek error {resp.status_code}: {resp.text[:200]}"
    except Exception as e:
        return f"DeepSeek error: {e}"


def _cache_fresh(context: ContextTypes.DEFAULT_TYPE, max_age=3600) -> bool:
    cached_at = context.bot_data.get("cached_at", 0)
    return (time.time() - cached_at) < max_age


def _get_products(context: ContextTypes.DEFAULT_TYPE):
    return context.bot_data.get("products", [])


def _set_products(context: ContextTypes.DEFAULT_TYPE, products):
    context.bot_data["products"] = products
    context.bot_data["cached_at"] = time.time()


async def _send(update: Update, text: str):
    """Send text in ≤4000-char chunks with Markdown."""
    for i in range(0, len(text), 4000):
        try:
            await update.message.reply_text(text[i:i+4000], parse_mode="Markdown")
        except Exception:
            await update.message.reply_text(text[i:i+4000])
        await asyncio.sleep(0.3)


# ── Sourcing report ───────────────────────────────────────────────────────────

def _build_report(top_products, budget: float, seasonal_products, next_keywords) -> str:
    next_month_num = datetime.now().month % 12 + 1
    month_names = ["", "January", "February", "March", "April", "May",
                   "June", "July", "August", "September", "October", "November", "December"]
    next_month = month_names[next_month_num]
    current_month = datetime.now().strftime("%B")

    products_text = ""
    for i, p in enumerate(top_products[:12], 1):
        products_text += (
            f"\n{i}. {p['name'][:55]}"
            f"\n   Uzum price: ${p['price_usd']:.2f} ({p['price_uzs']:,} UZS)"
            f"\n   Rating: {p['rating']} | Reviews: {p['reviews']} | Category: {p['category']}"
            f"\n   Score: {p['score']}/100\n"
        )

    seasonal_text = "\n".join([f"  • {p['name'][:50]} — ${p['price_usd']:.2f}" for p in seasonal_products[:5]]) or "None matched"

    prompt = f"""You are an expert e-commerce sourcing advisor for the Uzbekistan market (Uzum.uz).

USER BUDGET: ${budget:.0f} USD
TODAY: {datetime.now().strftime('%B %d, %Y')} ({current_month})
NEXT MONTH: {next_month} — trending: {', '.join(next_keywords)}

=== LIVE UZUM TOP PRODUCTS ===
{products_text}

=== SEASONAL PICKS FOR NEXT MONTH ===
{seasonal_text}

Produce a SHORT, ACTIONABLE sourcing report. No fluff. Money-focused.

Format EXACTLY like this:

🏆 TOP 3 PRODUCTS TO SOURCE NOW

1️⃣ [Product Name]
• Uzum sell price: $X
• Alibaba/1688 buy price: ~$X
• Shipping to UZ/KZ: ~$X/unit (air freight)
• Your landed cost: ~$X/unit
• Units to buy with ${budget:.0f}: ~X units
• Profit per unit: $X | Total profit: ~$X | ROI: ~X%
• ✅ Action: [1 sentence — exactly what to do]

2️⃣ ...

3️⃣ ...

📅 1 PRODUCT TO STOCK NOW FOR {next_month.upper()}
[Same format + 1 line seasonal angle]

⚠️ AVOID
[1-2 products to skip — 1 line each]

💡 BOTTOM LINE
[2-3 sentences: best use of ${budget:.0f} for max profit]

Be realistic. Air freight to UZ/KZ is ~$4-8/kg."""

    return _deepseek(prompt, max_tokens=1600)


# ── Core runner ───────────────────────────────────────────────────────────────

async def _run_report(update: Update, context: ContextTypes.DEFAULT_TYPE, budget: float):
    import glob
    report_files = sorted(glob.glob("/app/uzum_report_*.json"))
    if report_files:
        latest = report_files[-1]
        age = time.time() - os.path.getmtime(latest)
        if age < 3600:
            with open(latest, "r") as f:
                data = json.load(f)
            products = data.get("all_products", [])
            if products:
                await update.message.reply_text("📦 Using cached report (less than 1 hour old)...")
                products = [score_product(p) for p in products]
                _set_products(context, products)

    if _cache_fresh(context):
        products = _get_products(context)
        await update.message.reply_text("📦 Using cached market data (< 1h old)...")
    else:
        sent_msg = await update.message.reply_text("📡 Starting scrape...")
        
        async def send_progress(text):
            try:
                await sent_msg.edit_text(text)
            except Exception:
                pass
        
        loop = asyncio.get_event_loop()
        import functools
        products = await loop.run_in_executor(None, functools.partial(scrape_all_categories, pages_per_category=2, progress_callback=lambda t: asyncio.run_coroutine_threadsafe(send_progress(t), loop).result()))
        if not products:
            await update.message.reply_text("❌ Could not fetch Uzum data. Check your UZUM token.")
            return
        products = [score_product(p) for p in products]
        _set_products(context, products)

    total = len(products)
    top = sorted(products, key=lambda x: x["score"], reverse=True)
    seasonal, next_kw = get_seasonal_prediction(products)
    seasonal_sorted = sorted(seasonal, key=lambda x: x["score"], reverse=True)

    await update.message.reply_text(
        f"✅ Analyzed *{total}* products across *{len(CATEGORIES)}* categories\n"
        f"🤖 Generating your sourcing report...",
        parse_mode="Markdown",
    )

    report = _build_report(top, budget, seasonal_sorted, next_kw)
    header = (
        f"📊 *UZUM SOURCING REPORT*\n"
        f"_{datetime.now().strftime('%d %b %Y, %H:%M')}_\n"
        f"💰 Budget: *${budget:,.0f}* | Analyzed: *{total}* products\n"
        f"━━━━━━━━━━━━━━━━━━━\n\n"
    )
    await _send(update, header + report)
    await update.message.reply_text(
        "💬 *Have questions? Just type anything!*\n"
        "Examples:\n"
        "• _Which products for July?_\n"
        "• _How much profit on 100 sunscreens?_\n"
        "• _Best category for $2000?_",
        parse_mode="Markdown",
    )


# ── Command handlers ──────────────────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    budget = context.user_data.get("budget") or read_budget()
    if budget and budget != 1000.0:
        budget_info = f"\n\n💰 Your saved budget is *${budget:,.0f}*. Run /report to get your analysis."
    else:
        budget_info = "\n\n💰 Default budget is *$1,000*. Send me a number or use /budget to set yours."
    await update.message.reply_text(
        "👋 *Hello! I'm your Uzum Sourcing Assistant!*\n\n"
        "I analyze Uzum.uz, compare prices with Alibaba/1688, and help you find "
        "profitable products to import and sell in Uzbekistan.\n\n"
        "💬 *You can just chat with me* — ask about products, pricing, seasons, "
        "or anything sourcing-related. I'm here to help!\n\n"
        "💰 Or, if you want a full *sourcing report* with profit analysis, "
        "just send me your budget as a number (e.g. *1000*) and I'll do the rest."
        f"{budget_info}",
        parse_mode="Markdown",
    )


async def cmd_report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    budget = context.user_data.get("budget") or read_budget()
    await update.message.reply_text(f"⏳ Running analysis with budget *${budget:,.0f}*...", parse_mode="Markdown")
    await _run_report(update, context, budget)


async def cmd_top(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("⏳ Fetching top products from Uzum...")
    if _cache_fresh(context):
        products = _get_products(context)
    else:
        products = []
        for cat_name, cat_id in list(CATEGORIES.items())[:6]:
            prods, _ = scrape_category(cat_name, cat_id, offset=0, limit=48)
            products.extend(prods)
        products = [score_product(p) for p in products]
        _set_products(context, products)

    if not products:
        await update.message.reply_text("❌ Could not fetch products. Check your Uzum token.")
        return

    top = sorted(products, key=lambda x: x["score"], reverse=True)[:5]
    now = datetime.now().strftime("%d %b %Y, %H:%M")
    msg = f"🏆 *TOP 5 OPPORTUNITIES RIGHT NOW*\n_{now}_\n\n"
    for i, p in enumerate(top, 1):
        msg += (
            f"*{i}. {p['name'][:45]}*\n"
            f"💰 ${p['price_usd']:.2f} | ⭐ {p['rating']} | 📝 {p['reviews']} reviews\n"
            f"📂 {p['category']} | 🎯 {p['score']}/100\n"
            f"{p['flags']}\n\n"
        )
    await _send(update, msg)


async def cmd_budget(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    if not args:
        current = context.user_data.get("budget", "not set")
        await update.message.reply_text(
            f"Current budget: *${current}*\nUsage: `/budget 2000`",
            parse_mode="Markdown",
        )
        return
    try:
        budget = float(args[0].replace("$", "").replace(",", ""))
        if budget <= 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text("❌ Invalid. Example: `/budget 2000`", parse_mode="Markdown")
        return
    context.user_data["budget"] = budget
    save_budget(budget)
    await update.message.reply_text(
        f"✅ Budget set to *${budget:,.0f}*\nRun /report to get your analysis.",
        parse_mode="Markdown",
    )


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cached_at = context.bot_data.get("cached_at")
    cache_info = f"✅ Cache: {int((time.time() - cached_at) / 60)} min ago" if cached_at else "❌ No cache yet"
    budget = context.user_data.get("budget", "not set")
    api_ok = "✅ Ready" if os.getenv("DEEPSEEK_API_KEY", "") else "❌ Missing"
    await update.message.reply_text(
        f"🤖 *Bot Status*\n\n"
        f"💰 Budget: ${budget}\n"
        f"{cache_info}\n"
        f"🧠 DeepSeek AI: {api_ok}\n"
        f"📅 {datetime.now().strftime('%d %b %Y, %H:%M')}",
        parse_mode="Markdown",
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🤖 *Uzum Sourcing Bot — Commands*\n\n"
        "/start — Set budget & get full sourcing report\n"
        "/report — Re-run analysis with your saved budget\n"
        "/top — Quick top 5 products (fast, no AI)\n"
        "/budget 2000 — Update your budget\n"
        "/status — Bot health check\n"
        "/help — This message\n\n"
        "💬 *Or just chat with me!*\n"
        "• _Which products to source for July?_\n"
        "• _How much profit if I buy 100 sunscreens?_\n"
        "• _Best category for $2000 budget?_\n"
        "• _Compare lipstick vs face cream margins_",
        parse_mode="Markdown",
    )


# ── Free AI chat ──────────────────────────────────────────────────────────────

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle all text messages — budget input OR free AI chat."""
    text = update.message.text.strip()

    # Check if message looks like a budget number (e.g. "1000", "$500", "1,500")
    if text.replace("$", "").replace(",", "").replace(".", "").replace(" ", "").isdigit():
        try:
            budget = float(text.replace("$", "").replace(",", "").replace(" ", ""))
            if budget > 0:
                save_budget(budget)
                context.user_data["budget"] = budget
                await update.message.reply_text(
                    f"✅ Budget set: *${budget:,.0f}*\n\n⏳ Scraping Uzum market data...",
                    parse_mode="Markdown",
                )
                await _run_report(update, context, budget)
                return
        except ValueError:
            pass  # Not a number — let DeepSeek handle it

    # Free AI chat — show typing indicator instead of text message
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")

    budget = context.user_data.get("budget", 1000)

    products = _get_products(context)
    market_context = ""
    if products:
        top = sorted(products, key=lambda x: x["score"], reverse=True)[:10]
        market_context = "=== CURRENT UZUM MARKET DATA ===\n"
        for p in top:
            market_context += f"• {p['name'][:50]} — ${p['price_usd']:.2f} | ⭐{p['rating']} | {p['reviews']} reviews | {p['category']}\n"
    else:
        market_context = "(No live data yet — run /start or /report for live market data)"

    current_month = datetime.now().strftime("%B")
    next_month_num = datetime.now().month % 12 + 1
    month_names = ["", "January", "February", "March", "April", "May",
                   "June", "July", "August", "September", "October", "November", "December"]
    next_month = month_names[next_month_num]
    seasonal_now = SEASONAL_CALENDAR.get(datetime.now().month, [])
    seasonal_next = SEASONAL_CALENDAR.get(next_month_num, [])

    prompt = f"""You are a friendly, helpful AI assistant for a Telegram bot called "Uzum Sourcing Assistant".

FIRST RULE: Be conversational and natural, just like a normal person.
- If the user says "hi", "hello", "salom", "what's up" or any greeting — GREET THEM BACK warmly. Ask how you can help. Do NOT jump into business advice.
- Let the USER lead the conversation. Only talk about sourcing, products, or markets if the user specifically asks about it.
- If the user asks a casual question ("how are you?", "who made you?"), answer casually.
- Never assume the user wants a business analysis unless they explicitly ask for one.

WHEN the user does ask about sourcing (products, profits, pricing, seasons, etc.):
You have access to this context:
- User's budget (if known): ${budget:,.0f} USD (defaults to $1,000 if not set)
- Today: {datetime.now().strftime('%B %d, %Y')}
- Current month ({current_month}) trends: {', '.join(seasonal_now)}
- Next month ({next_month}) trends: {', '.join(seasonal_next)}

{market_context}

General knowledge you can use:
- Alibaba/1688 prices are typically 3-8x cheaper than Uzbekistan retail
- Air freight to UZ/KZ: ~$4-8/kg, 7-14 days
- Sea freight: ~$1-2/kg, 30-45 days
- 1 USD ≈ 12,700 UZS

USER MESSAGE: {text}

Respond naturally. Be friendly. Use emojis sparingly. Format for Telegram (*bold*, _italic_). Max 500 words."""

    response = _deepseek(prompt, max_tokens=900)
    await _send(update, response)


# ── Main ──────────────────────────────────────────────────────────────────────

async def _main_async():
    token = TELEGRAM_BOT_TOKEN or os.getenv("TELEGRAM_BOT_TOKEN", "")
    if not token:
        print("❌ TELEGRAM_BOT_TOKEN is not set.")
        return

    print("🚀 Starting Uzum Sourcing Bot...")
    print(f"   DeepSeek AI: {'✅ Ready' if os.getenv('DEEPSEEK_API_KEY', '') else '❌ Missing DEEPSEEK_API_KEY'}")
    print(f"   Telegram:  ✅ Token found")

    app = Application.builder().token(token).build()

    # Register commands
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("report", cmd_report))
    app.add_handler(CommandHandler("top", cmd_top))
    app.add_handler(CommandHandler("budget", cmd_budget))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("help", cmd_help))

    # All text messages (budget input + free chat)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    print("✅ Bot is running. Press Ctrl+C to stop.\n")
    async with app:
        await app.initialize()
        # Set bot commands for the / menu
        await app.bot.set_my_commands([
            BotCommand("start", "Set budget & get sourcing report"),
            BotCommand("report", "Re-run analysis with saved budget"),
            BotCommand("top", "Quick top 5 products (fast)"),
            BotCommand("budget", "Update budget: /budget 2000"),
            BotCommand("status", "Bot health check"),
            BotCommand("help", "Show all commands"),
        ])
        await app.start()
        await app.updater.start_polling(allowed_updates=Update.ALL_TYPES)

        async def daily_report():
            import datetime as dt
            while True:
                now = dt.datetime.utcnow()
                target = now.replace(hour=2, minute=0, second=0, microsecond=0)
                if now >= target:
                    target += dt.timedelta(days=1)
                wait_seconds = (target - now).total_seconds()
                await asyncio.sleep(wait_seconds)
                try:
                    chat_id = int(os.getenv("TELEGRAM_CHAT_ID", "687396965"))
                    budget = read_budget()

                    class FakeUpdate:
                        class message:
                            @staticmethod
                            async def reply_text(text, **kwargs):
                                await app.bot.send_message(chat_id=chat_id, text=text, **kwargs)
                            @staticmethod
                            async def edit_text(text, **kwargs):
                                await app.bot.send_message(chat_id=chat_id, text=text, **kwargs)
                        class effective_chat:
                            id = chat_id

                    class FakeContext:
                        bot_data = {}
                        user_data = {}
                        bot = app.bot

                    await app.bot.send_message(chat_id=chat_id, text="🌅 *Good morning! Daily Uzum report starting...*", parse_mode="Markdown")
                    await _run_report(FakeUpdate(), FakeContext(), budget)
                except Exception as e:
                    print(f"Daily report error: {e}")

        asyncio.create_task(daily_report())

        try:
            await asyncio.Event().wait()
        except (KeyboardInterrupt, SystemExit):
            pass
        finally:
            await app.updater.stop()
            await app.stop()
            await app.shutdown()


def main():
    asyncio.run(_main_async())


if __name__ == "__main__":
    main()