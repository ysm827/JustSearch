import os
import json
import logging
import glob
import aiofiles
from datetime import datetime
import asyncio

logger = logging.getLogger(__name__)

# Define paths relative to the project root (one level up from src)
PROJECT_ROOT = os.path.dirname(os.path.dirname(__file__))
CHATS_DIR = os.path.join(PROJECT_ROOT, 'chats')
os.makedirs(CHATS_DIR, exist_ok=True)

async def load_chat_history(file_path):
    if not os.path.exists(file_path):
        return None
    try:
        async with aiofiles.open(file_path, 'r', encoding='utf-8') as f:
            content = await f.read()
            return json.loads(content)
    except Exception as e:
        logger.error("加载对话失败：%s", e)
        return None

async def save_chat_history(session_id, messages, title=None):
    if not messages:
        return
    
    file_path = os.path.join(CHATS_DIR, f"{session_id}.json")
    
    if not title:
        if os.path.exists(file_path):
            try:
                async with aiofiles.open(file_path, 'r', encoding='utf-8') as f:
                    content = await f.read()
                    data = json.loads(content)
                    title = data.get('title', '新对话')
            except (json.JSONDecodeError, KeyError, UnicodeDecodeError):
                title = messages[0]['content'][:30] + "..." if messages else "新对话"
        else:
            title = messages[0]['content'][:30] + "..." if messages else "新对话"
            
    data = {
        "id": session_id,
        "title": title,
        "timestamp": datetime.now().isoformat(),
        "messages": messages
    }
    
    async with aiofiles.open(file_path, 'w', encoding='utf-8') as f:
        await f.write(json.dumps(data, ensure_ascii=False, indent=2))

async def list_chats():
    files = glob.glob(os.path.join(CHATS_DIR, "*.json"))
    chats = []
    
    async def read_chat_meta(f):
        try:
            async with aiofiles.open(f, 'r', encoding='utf-8') as fd:
                content = await fd.read()
                data = json.loads(content)
                return {
                    "id": data.get("id", os.path.basename(f).replace(".json", "")),
                    "title": data.get("title", "无标题对话"),
                    "timestamp": data.get("timestamp", "")
                }
        except (json.JSONDecodeError, KeyError, UnicodeDecodeError):
            return None

    tasks = [read_chat_meta(f) for f in files]
    if tasks:
        results = await asyncio.gather(*tasks)
        chats = [r for r in results if r is not None]
    
    chats.sort(key=lambda x: x['timestamp'], reverse=True)
    return chats

async def delete_chat(session_id):
    file_path = os.path.join(CHATS_DIR, f"{session_id}.json")
    if os.path.exists(file_path):
        await asyncio.to_thread(os.remove, file_path)

async def delete_all_chats():
    files = glob.glob(os.path.join(CHATS_DIR, "*.json"))
    for f in files:
        try:
            await asyncio.to_thread(os.remove, f)
        except Exception as e:
            logger.error("Failed to delete %s: %s", f, e)

def get_chat_path(session_id):
    return os.path.join(CHATS_DIR, f"{session_id}.json")