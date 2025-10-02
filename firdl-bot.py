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
    message = f"""
*🇮🇳 Fazilka FIR Downloader*
_{'Your instant portal to PPSAANJH documents\.'}_
{separator}
*🚀 How It Works*
This bot retrieves FIRs using a high\-speed cache\. If a file has been downloaded before, you'll get it instantly\.

*✅ To Get an FIR*
Just send a message like this:
`code fir_number/year`

*Example:* `cf 123/2025`

*📚 For a list of all station codes, use the /help command\.*
"""
    await update.message.reply_text(message, parse_mode=ParseMode.MARKDOWN_V2)

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    separator = r"\-\-\-\-\-\-\-\-\-\-\-\-"
    ps_list = "\n".join([f"`{code}` \\- _{data['psName'].replace('-', r'\\-')}_" for code, data in psMap.items()])
    message = f"""
*📖 BOT GUIDE \| FAZILKA DISTRICT*
This bot uses a permanent cache for instant delivery of previously downloaded files\.

*SINGLE DOWNLOAD*
`ps_code fir_number/year`
*Example:* `cf 123/2025`
{separator}
*BULK DOWNLOAD*
Send each request on a new line\. While there's no hard limit, please be reasonable\.
*Example:*
