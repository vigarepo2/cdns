import os
import logging
import asyncio
import random
import requests
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from telegram.constants import ParseMode
import motor.motor_asyncio
import re

# --- CONFIGURATION ---
TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
LOG_CHANNEL_ID = os.getenv('LOG_CHANNEL_ID')
MONGO_URI = os.getenv('MONGO_URI')
MONGO_DB_NAME = os.getenv('MONGO_DB_NAME', 'fir_bot')

FIR_API_URL = 'https://app.ppsaanjh.in:7071/Citizen_apis/SaanjhWS/AppService/DownloadFIR_cctns'
BULK_DELAY = 1.5  # Delay in seconds between processing bulk requests

# --- LOGGING SETUP ---
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- POLICE STATION DATA ---
psMap = {
    'ak': {'districtCode': '25810', 'psCode': '25810014', 'psName': 'Amir Khas'},
    'aw': {'districtCode': '25810', 'psCode': '25810005', 'psName': 'Arniwala'},
    'bw': {'districtCode': '25810', 'psCode': '25810010', 'psName': 'Bahawala'},
    'c1a': {'districtCode': '25810', 'psCode': '25810006', 'psName': 'City 1 Abohar'},
    'c2a': {'districtCode': '25810', 'psCode': '25810007', 'psName': 'City 2 Abohar'},
    'cf': {'districtCode': '25810', 'psCode': '25810003', 'psName': 'City Fazilka'},
    'cj': {'districtCode': '25810', 'psCode': '25810001', 'psName': 'City Jalalabad'},
    'ccf': {'districtCode': '25810', 'psCode': '25810015', 'psName': 'Cyber Crime Fazilka'},
    'kk': {'districtCode': '25810', 'psCode': '25525037', 'psName': 'Khui Khera'},
    'ks': {'districtCode': '25810', 'psCode': '25810009', 'psName': 'Khuian Sarwar'},
    'sa': {'districtCode': '25810', 'psCode': '25810008', 'psName': 'Sadar Abohar'},
    'sf': {'districtCode': '25810', 'psCode': '25810004', 'psName': 'Sadar Fazilka'},
    'sj': {'districtCode': '25810', 'psCode': '25810002', 'psName': 'Sadar Jalalabad'},
    'vk': {'districtCode': '25810', 'psCode': '25810002', 'psName': 'Vairoke'},
    'ssocf': {'districtCode': '25524', 'psCode': '25810011', 'psName': 'SSOC Fazilka'},
    'ssoca': {'districtCode': '25524', 'psCode': '25524002', 'psName': 'SSOC Amritsar'},
    'ssocm': {'districtCode': '25524', 'psCode': '25524002', 'psName': 'SSOC SAS Nagar'},
}

# --- DATABASE HELPERS (MONGODB) ---
async def get_cached_message_id(collection, fir_key):
    document = await collection.find_one({"_id": fir_key})
    return document['message_id'] if document else None

async def cache_message_id(collection, fir_key, message_id):
    # Using replace_one with upsert is an efficient way to insert or update.
    await collection.replace_one(
        {"_id": fir_key},
        {"_id": fir_key, "message_id": message_id},
        upsert=True
    )
    logger.info(f"Cached message_id {message_id} for key {fir_key}")

# --- TELEGRAM COMMAND HANDLERS ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    separator = r"\-\-\-\-\-\-\-\-\-\-\-\-"
    # Use .format() to avoid f-string parsing errors with markdown escapes
    message_template = """
*🇮🇳 Fazilka FIR Downloader*
_{'Your instant portal to PPSAANJH documents\.'}_
{sep}
*🚀 How It Works*
This bot retrieves FIRs using a high\-speed cache\. If a file has been downloaded before, you'll get it instantly\.

*✅ To Get an FIR*
Just send a message like this:
`code fir_number/year`

*Example:* `cf 123/2025`

*📚 For a list of all station codes, use the /help command\.*
"""
    message = message_template.format(sep=separator)
    await update.message.reply_text(message, parse_mode=ParseMode.MARKDOWN_V2)

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    separator = r"\-\-\-\-\-\-\-\-\-\-\-\-"
    ps_list = "\n".join([f"`{code}` \\- _{data['psName'].replace('-', r'\\-')}_" for code, data in psMap.items()])
    # Use .format() to avoid f-string parsing errors with markdown escapes
    message_template = """
*📖 BOT GUIDE \| FAZILKA DISTRICT*
This bot uses a permanent cache for instant delivery of previously downloaded files\.

*SINGLE DOWNLOAD*
`ps_code fir_number/year`
*Example:* `cf 123/2025`
{sep}
*BULK DOWNLOAD*
Send each request on a new line\. While there's no hard limit, please be reasonable\.
*Example:*
cf 123/2025
aw 45/2024
{sep}
*📍 AVAILABLE STATION CODES*
{ps_list}
"""
    message = message_template.format(sep=separator, ps_list=ps_list)
    await update.message.reply_text(message, parse_mode=ParseMode.MARKDOWN_V2)

# --- CORE LOGIC ---
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles incoming text messages for single or bulk FIR requests."""
    lines = [line.strip() for line in update.message.text.strip().split('\n') if line.strip()]
    if not lines:
        return

    if len(lines) > 1:
        await handle_bulk_request(update, context, lines)
    else:
        await handle_single_request(update, context, lines[0])

async def handle_single_request(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str):
    status_msg = await update.message.reply_text("⏳ _Processing request\\.\\._", parse_mode=ParseMode.MARKDOWN_V2)
    await process_fir_request(update.message.chat_id, text, context)
    await context.bot.delete_message(update.message.chat_id, status_msg.message_id)

async def handle_bulk_request(update: Update, context: ContextTypes.DEFAULT_TYPE, lines: list):
    chat_id = update.message.chat_id
    status_msg = await context.bot.send_message(
        chat_id, f"➡️ *Starting bulk processing for {len(lines)} tasks\\.*", parse_mode=ParseMode.MARKDOWN_V2
    )
    for line in lines:
        await process_fir_request(chat_id, line, context)
        await asyncio.sleep(BULK_DELAY)
    
    await context.bot.edit_message_text(
        chat_id=chat_id,
        message_id=status_msg.message_id,
        text=f"✅ *Bulk processing complete for {len(lines)} tasks\\.*",
        parse_mode=ParseMode.MARKDOWN_V2
    )

async def process_fir_request(chat_id: int, text: str, context: ContextTypes.DEFAULT_TYPE):
    db_collection = context.bot_data["db_collection"]
    match = re.match(r'^([a-z0-9]+)\s*(\d+)/(\d{4})$', text.lower().strip())
    
    if not match:
        await context.bot.send_message(chat_id, f"❌ *Invalid Format:* `{text}`", parse_mode=ParseMode.MARKDOWN_V2)
        return

    ps_short_code, fir_number, fir_year = match.groups()
    ps_data = psMap.get(ps_short_code)

    # --- Validation ---
    if not ps_data or not (1 <= int(fir_number) <= 999) or not (2010 <= int(fir_year) <= 2026):
        await context.bot.send_message(chat_id, f"❌ *Invalid Input:* `{text}`", parse_mode=ParseMode.MARKDOWN_V2)
        return
    
    fir_key = f"FIR_{ps_short_code}_{fir_number}_{fir_year}"
    cached_id = await get_cached_message_id(db_collection, fir_key)

    if cached_id:
        logger.info(f"Cache hit for {fir_key}.")
        await context.bot.copy_message(chat_id, from_chat_id=LOG_CHANNEL_ID, message_id=cached_id)
        return
    
    # --- API Fetching ---
    logger.info(f"Cache miss for {fir_key}. Fetching from API.")
    spoof_ip, ip_version = generate_spoofed_ip()
    pdf_content, status_code = await fetch_fir_from_api(ps_data, fir_number, fir_year, spoof_ip)

    if pdf_content:
        file_name = f"FIR.{fir_number}.{fir_year}.{ps_data['psName'].replace(' ', '-')}.pdf"
        user_caption = generate_success_caption(ps_data, fir_number, fir_year, spoof_ip, ip_version)
        await context.bot.send_document(chat_id, document=pdf_content, filename=file_name, caption=user_caption, parse_mode=ParseMode.MARKDOWN_V2)
        
        # Log to channel and cache the new message ID
        log_caption = f"#{fir_key}\nPS: {ps_data['psName']}\nFIR: {fir_number}/{fir_year}"
        log_message = await context.bot.send_document(LOG_CHANNEL_ID, document=pdf_content, filename=file_name, caption=log_caption)
        
        if log_message and log_message.message_id:
            await cache_message_id(db_collection, fir_key, log_message.message_id)
    else:
        error_message = generate_api_error_message(status_code, spoof_ip, ip_version)
        await context.bot.send_message(chat_id, error_message, parse_mode=ParseMode.MARKDOWN_V2)

# --- API & UTILITY FUNCTIONS ---
def generate_spoofed_ip():
    if random.random() < 0.5:
        ip = ".".join(map(str, (random.randint(0, 255) for _ in range(4))))
        return ip, "IPv4"
    ip = ":".join(f'{random.randint(0, 65535):x}' for _ in range(8))
    return ip, "IPv6"

async def fetch_fir_from_api(ps_data, fir_number, fir_year, spoof_ip):
    """Performs the network request to the external FIR API."""
    form_data = {'district': ps_data['districtCode'], 'ps': ps_data['psCode'], 'firNo': fir_number, 'firYear': fir_year}
    headers = {'Content-Type': 'application/x-www-form-urlencoded', 'User-Agent': 'Python-Telegram-Bot/1.0', 'X-Forwarded-For': spoof_ip, 'X-Real-IP': spoof_ip}
    
    try:
        loop = asyncio.get_event_loop()
        response = await loop.run_in_executor(
            None, lambda: requests.post(FIR_API_URL, data=form_data, headers=headers, timeout=20)
        )
        response.raise_for_status()
        if 'application/pdf' in response.headers.get('Content-Type', '') and len(response.content) > 1000:
            return response.content, response.status_code
        return None, 404
    except requests.exceptions.RequestException as e:
        status = e.response.status_code if e.response else 500
        logger.error(f"API request failed with status {status}: {e}")
        return None, status

def generate_success_caption(ps, num, year, ip, ver):
    sep = r"\-\-\-\-\-\-\-\-\-\-\-\-"
    return f"*✅ FILE RETRIEVED*\n{sep}\n*📄 Details:*\n› *PS:* {ps['psName']}\n› *FIR No:* {num}\n› *Year:* {year}\n*🌐 Network:*\n› *IP:* `{ip}` \({ver}\)\n{sep}"

def generate_api_error_message(status, ip, ver):
    errors = {404: "🚫 *Not Found*", 403: "🛑 *Forbidden*"}
    msg = errors.get(status, f"🔧 *Server Error \\({status}\\)*")
    return f"{msg}\n\n*IP Used:* `{ip}` \({ver}\)"

# --- MAIN APPLICATION ---
def main():
    if not all([TELEGRAM_BOT_TOKEN, LOG_CHANNEL_ID, MONGO_URI]):
        logger.critical("Missing critical environment variables! Please set TELEGRAM_BOT_TOKEN, LOG_CHANNEL_ID, and MONGO_URI.")
        return

    # Initialize MongoDB client
    mongo_client = motor.motor_asyncio.AsyncIOMotorClient(MONGO_URI)
    db = mongo_client[MONGO_DB_NAME]
    db_collection = db.fir_logs

    # Create the bot application
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    application.bot_data["db_collection"] = db_collection

    # Register handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    # Start polling
    logger.info("Bot is starting with polling...")
    application.run_polling()

if __name__ == '__main__':
    main()
