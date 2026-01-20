# -*- coding: utf-8 -*-
"""
===================================
Telegram 平台适配器
===================================

处理 Telegram Bot 的 Webhook 更新。

Telegram Bot API 文档：
https://core.telegram.org/bots/api
"""

import hashlib
import hmac
import logging
import threading
from datetime import datetime
from typing import Dict, Any, Optional

from bot.platforms.base import BotPlatform
from bot.models import BotMessage, BotResponse, WebhookResponse, ChatType

logger = logging.getLogger(__name__)


class TelegramPlatform(BotPlatform):
    """
    Telegram 平台适配器
    
    支持：
    - Webhook 更新处理
    - 消息解析
    - 回复消息发送
    
    配置要求：
    - TELEGRAM_BOT_TOKEN: Bot Token（从 @BotFather 获取）
    - TELEGRAM_WEBHOOK_SECRET: Webhook 密钥（可选，用于验证请求）
    
    Webhook 设置：
    使用 setWebhook API 设置回调 URL：
    https://api.telegram.org/bot<token>/setWebhook?url=<webhook_url>&secret_token=<secret>
    """
    
    def __init__(self):
        from config import get_config
        config = get_config()
        
        self._bot_token = getattr(config, 'telegram_bot_token', None)
        self._webhook_secret = getattr(config, 'telegram_webhook_secret', None)
        self._chat_id = getattr(config, 'telegram_chat_id', None)
        
        # Bot 用户名（用于识别 @提及）
        self._bot_username = None
    
    @property
    def platform_name(self) -> str:
        return "telegram"
    
    def verify_request(self, headers: Dict[str, str], body: bytes) -> bool:
        """
        验证 Telegram Webhook 请求
        
        Telegram 使用 X-Telegram-Bot-Api-Secret-Token 头验证请求。
        """
        if not self._webhook_secret:
            logger.debug("[Telegram] 未配置 webhook_secret，跳过验证")
            return True
        
        secret_token = headers.get('X-Telegram-Bot-Api-Secret-Token', '')
        
        if secret_token != self._webhook_secret:
            logger.warning("[Telegram] Webhook 密钥验证失败")
            return False
        
        return True
    
    def handle_challenge(self, data: Dict[str, Any]) -> Optional[WebhookResponse]:
        """Telegram 不需要 URL 验证"""
        return None
    
    def parse_message(self, data: Dict[str, Any]) -> Optional[BotMessage]:
        """
        解析 Telegram 更新
        
        Telegram Update 格式：
        {
            "update_id": 123456,
            "message": {
                "message_id": 123,
                "from": {
                    "id": 123456789,
                    "is_bot": false,
                    "first_name": "John",
                    "last_name": "Doe",
                    "username": "johndoe"
                },
                "chat": {
                    "id": -123456789,
                    "title": "群名",
                    "type": "group"  # private, group, supergroup, channel
                },
                "date": 1234567890,
                "text": "/analyze 600519",
                "entities": [
                    {"type": "bot_command", "offset": 0, "length": 8},
                    {"type": "mention", "offset": 10, "length": 5}
                ]
            }
        }
        """
        # 获取消息对象
        message_data = data.get('message') or data.get('edited_message')
        
        if not message_data:
            # 可能是其他类型的更新（callback_query, inline_query 等）
            logger.debug("[Telegram] 忽略非消息更新")
            return None
        
        # 只处理文本消息
        text = message_data.get('text', '')
        if not text:
            logger.debug("[Telegram] 忽略非文本消息")
            return None
        
        # 获取发送者信息
        from_user = message_data.get('from', {})
        
        # 获取用户名
        user_name = from_user.get('first_name', '')
        if from_user.get('last_name'):
            user_name += f" {from_user['last_name']}"
        if not user_name:
            user_name = from_user.get('username', str(from_user.get('id', '')))
        
        # 获取会话信息
        chat = message_data.get('chat', {})
        chat_type_str = chat.get('type', '')
        
        if chat_type_str == 'private':
            chat_type = ChatType.PRIVATE
        elif chat_type_str in ('group', 'supergroup'):
            chat_type = ChatType.GROUP
        else:
            chat_type = ChatType.UNKNOWN
        
        # 检查是否 @了机器人
        entities = message_data.get('entities', [])
        mentioned = self._check_mention(text, entities)
        
        # 提取命令
        content = self._extract_command(text, entities)
        
        # 创建时间
        date = message_data.get('date', 0)
        try:
            timestamp = datetime.fromtimestamp(date)
        except (ValueError, TypeError):
            timestamp = datetime.now()
        
        return BotMessage(
            platform=self.platform_name,
            message_id=str(message_data.get('message_id', '')),
            user_id=str(from_user.get('id', '')),
            user_name=user_name,
            chat_id=str(chat.get('id', '')),
            chat_type=chat_type,
            content=content,
            raw_content=text,
            mentioned=mentioned,
            mentions=self._extract_mentions(text, entities),
            timestamp=timestamp,
            raw_data=data,
        )
    
    def _check_mention(self, text: str, entities: list) -> bool:
        """检查是否 @了机器人"""
        for entity in entities:
            if entity.get('type') == 'mention':
                offset = entity.get('offset', 0)
                length = entity.get('length', 0)
                mention = text[offset:offset + length]
                
                # 检查是否是机器人的用户名
                if self._bot_username and mention.lower() == f"@{self._bot_username.lower()}":
                    return True
        
        return False
    
    def _extract_mentions(self, text: str, entities: list) -> list:
        """提取所有 @提及"""
        mentions = []
        for entity in entities:
            if entity.get('type') == 'mention':
                offset = entity.get('offset', 0)
                length = entity.get('length', 0)
                mention = text[offset:offset + length]
                if mention.startswith('@'):
                    mentions.append(mention[1:])  # 去掉 @
        return mentions
    
    def _extract_command(self, text: str, entities: list) -> str:
        """
        提取命令内容
        
        Telegram 的命令格式：/command@botname args
        """
        # 移除 @botname 部分
        if self._bot_username:
            text = text.replace(f"@{self._bot_username}", "")
        
        # 清理多余空格
        return ' '.join(text.split())
    
    def format_response(
        self, 
        response: BotResponse, 
        message: BotMessage
    ) -> WebhookResponse:
        """
        格式化 Telegram 响应
        
        Telegram Webhook 可以在响应中直接发送消息。
        响应格式：
        {
            "method": "sendMessage",
            "chat_id": 123456,
            "text": "回复内容",
            "parse_mode": "Markdown",
            "reply_to_message_id": 123
        }
        """
        if not response.text:
            return WebhookResponse.success()
        
        # 构建响应
        body = {
            "method": "sendMessage",
            "chat_id": int(message.chat_id) if message.chat_id.lstrip('-').isdigit() else message.chat_id,
            "text": response.text,
        }
        
        # Markdown 格式
        if response.markdown:
            body["parse_mode"] = "Markdown"
        
        # 回复原消息
        if response.reply_to_message and message.message_id:
            body["reply_to_message_id"] = int(message.message_id)
        
        return WebhookResponse.success(body)
    
    def send_message(
        self, 
        chat_id: str, 
        text: str,
        parse_mode: Optional[str] = None,
        reply_to_message_id: Optional[int] = None
    ) -> bool:
        """
        直接通过 API 发送消息
        
        用于异步发送或发送到其他会话。
        
        Args:
            chat_id: 目标会话 ID
            text: 消息内容
            parse_mode: 解析模式（Markdown, HTML, MarkdownV2）
            reply_to_message_id: 回复的消息 ID
            
        Returns:
            是否发送成功
        """
        if not self._bot_token:
            logger.warning("[Telegram] 未配置 bot_token")
            return False
        
        import requests
        
        try:
            api_url = f"https://api.telegram.org/bot{self._bot_token}/sendMessage"
            
            payload = {
                "chat_id": chat_id,
                "text": text,
            }
            
            if parse_mode:
                payload["parse_mode"] = parse_mode
            
            if reply_to_message_id:
                payload["reply_to_message_id"] = reply_to_message_id
            
            resp = requests.post(api_url, json=payload, timeout=10)
            
            if resp.status_code == 200:
                result = resp.json()
                if result.get('ok'):
                    logger.info("[Telegram] 消息发送成功")
                    return True
                else:
                    logger.error(f"[Telegram] API 返回错误: {result}")
                    return False
            else:
                logger.error(f"[Telegram] 请求失败: {resp.status_code}")
                return False
                
        except Exception as e:
            logger.error(f"[Telegram] 发送消息异常: {e}")
            return False
    
    def set_webhook(self, url: str, secret_token: Optional[str] = None) -> bool:
        """
        设置 Webhook URL
        
        Args:
            url: Webhook URL
            secret_token: 密钥（可选）
            
        Returns:
            是否设置成功
        """
        if not self._bot_token:
            logger.warning("[Telegram] 未配置 bot_token")
            return False
        
        import requests
        
        try:
            api_url = f"https://api.telegram.org/bot{self._bot_token}/setWebhook"
            
            payload = {"url": url}
            
            if secret_token:
                payload["secret_token"] = secret_token
            
            resp = requests.post(api_url, json=payload, timeout=10)
            
            if resp.status_code == 200:
                result = resp.json()
                if result.get('ok'):
                    logger.info(f"[Telegram] Webhook 设置成功: {url}")
                    return True
                else:
                    logger.error(f"[Telegram] 设置 Webhook 失败: {result}")
                    return False
            else:
                logger.error(f"[Telegram] 请求失败: {resp.status_code}")
                return False
                
        except Exception as e:
            logger.error(f"[Telegram] 设置 Webhook 异常: {e}")
            return False
    
    def delete_webhook(self) -> bool:
        """删除 Webhook"""
        if not self._bot_token:
            return False
        
        import requests
        
        try:
            api_url = f"https://api.telegram.org/bot{self._bot_token}/deleteWebhook"
            resp = requests.post(api_url, timeout=10)
            return resp.status_code == 200 and resp.json().get('ok', False)
        except Exception as e:
            logger.error(f"[Telegram] 删除 Webhook 异常: {e}")
            return False
    
    def get_bot_info(self) -> Optional[Dict]:
        """获取 Bot 信息"""
        if not self._bot_token:
            return None
        
        import requests
        
        try:
            api_url = f"https://api.telegram.org/bot{self._bot_token}/getMe"
            resp = requests.get(api_url, timeout=10)
            
            if resp.status_code == 200:
                result = resp.json()
                if result.get('ok'):
                    bot_info = result.get('result', {})
                    self._bot_username = bot_info.get('username')
                    return bot_info
            
            return None
            
        except Exception as e:
            logger.error(f"[Telegram] 获取 Bot 信息异常: {e}")
            return None
