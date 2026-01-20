# -*- coding: utf-8 -*-
"""
===================================
飞书平台适配器
===================================

处理飞书机器人的 Webhook 回调。

飞书机器人文档：
https://open.feishu.cn/document/ukTMukTMukTM/ucTM5YjL3ETO24yNxkjN
"""

import hashlib
import hmac
import base64
import json
import logging
import time
from datetime import datetime
from typing import Dict, Any, Optional

from bot.platforms.base import BotPlatform
from bot.models import BotMessage, BotResponse, WebhookResponse, ChatType

logger = logging.getLogger(__name__)


class FeishuPlatform(BotPlatform):
    """
    飞书平台适配器
    
    支持：
    - 事件订阅回调（机器人收到消息）
    - URL 验证（配置回调地址时的验证请求）
    - 消息签名验证
    
    配置要求：
    - FEISHU_APP_ID: 应用 ID
    - FEISHU_APP_SECRET: 应用密钥
    - FEISHU_VERIFICATION_TOKEN: 事件订阅验证 Token
    - FEISHU_ENCRYPT_KEY: 加密密钥（可选）
    """
    
    def __init__(self):
        from config import get_config
        config = get_config()
        
        self._app_id = getattr(config, 'feishu_app_id', None)
        self._app_secret = getattr(config, 'feishu_app_secret', None)
        self._verification_token = getattr(config, 'feishu_verification_token', None)
        self._encrypt_key = getattr(config, 'feishu_encrypt_key', None)
    
    @property
    def platform_name(self) -> str:
        return "feishu"
    
    def verify_request(self, headers: Dict[str, str], body: bytes) -> bool:
        """
        验证飞书请求签名
        
        飞书使用 X-Lark-Signature 头进行签名验证。
        签名算法：sha256(timestamp + nonce + encrypt_key + body)
        """
        if not self._verification_token:
            # 未配置验证 Token，跳过验证（开发环境）
            logger.warning("[Feishu] 未配置 verification_token，跳过签名验证")
            return True
        
        # 获取签名相关头
        timestamp = headers.get('X-Lark-Request-Timestamp', '')
        nonce = headers.get('X-Lark-Request-Nonce', '')
        signature = headers.get('X-Lark-Signature', '')
        
        if not signature:
            # 没有签名头，可能是旧版本或验证请求
            return True
        
        # 计算签名
        if self._encrypt_key:
            sign_string = f"{timestamp}{nonce}{self._encrypt_key}{body.decode('utf-8')}"
        else:
            sign_string = f"{timestamp}{nonce}{body.decode('utf-8')}"
        
        expected_signature = hashlib.sha256(sign_string.encode('utf-8')).hexdigest()
        
        if signature != expected_signature:
            logger.warning(f"[Feishu] 签名验证失败: expected={expected_signature}, got={signature}")
            return False
        
        return True
    
    def handle_challenge(self, data: Dict[str, Any]) -> Optional[WebhookResponse]:
        """
        处理飞书 URL 验证请求
        
        配置事件订阅时，飞书会发送验证请求：
        {
            "challenge": "xxx",
            "token": "xxx",
            "type": "url_verification"
        }
        
        需要返回：
        {"challenge": "xxx"}
        """
        if data.get('type') == 'url_verification':
            challenge = data.get('challenge', '')
            token = data.get('token', '')
            
            # 验证 token
            if self._verification_token and token != self._verification_token:
                logger.warning(f"[Feishu] 验证 token 不匹配")
                return WebhookResponse.error("Invalid token", 403)
            
            logger.info(f"[Feishu] URL 验证成功")
            return WebhookResponse.challenge(challenge)
        
        return None
    
    def parse_message(self, data: Dict[str, Any]) -> Optional[BotMessage]:
        """
        解析飞书消息
        
        飞书事件格式（v2.0）：
        {
            "schema": "2.0",
            "header": {
                "event_id": "xxx",
                "event_type": "im.message.receive_v1",
                "create_time": "1234567890",
                "token": "xxx",
                "app_id": "xxx"
            },
            "event": {
                "sender": {
                    "sender_id": {"open_id": "xxx", "user_id": "xxx"},
                    "sender_type": "user"
                },
                "message": {
                    "message_id": "xxx",
                    "chat_id": "xxx",
                    "chat_type": "group",
                    "message_type": "text",
                    "content": "{\"text\":\"@机器人 /analyze 600519\"}"
                }
            }
        }
        """
        # 检查事件类型
        header = data.get('header', {})
        event_type = header.get('event_type', '')
        
        if event_type != 'im.message.receive_v1':
            logger.debug(f"[Feishu] 忽略非消息事件: {event_type}")
            return None
        
        event = data.get('event', {})
        message_data = event.get('message', {})
        sender_data = event.get('sender', {})
        
        # 只处理文本消息
        message_type = message_data.get('message_type', '')
        if message_type != 'text':
            logger.debug(f"[Feishu] 忽略非文本消息: {message_type}")
            return None
        
        # 解析消息内容
        content_str = message_data.get('content', '{}')
        try:
            content_json = json.loads(content_str)
            raw_content = content_json.get('text', '')
        except json.JSONDecodeError:
            raw_content = content_str
        
        # 提取 @机器人 后的内容
        content = self._extract_command(raw_content, event)
        mentioned = '@' in raw_content or bool(message_data.get('mentions'))
        
        # 获取发送者信息
        sender_id = sender_data.get('sender_id', {})
        user_id = sender_id.get('open_id', '') or sender_id.get('user_id', '')
        
        # 获取会话类型
        chat_type_str = message_data.get('chat_type', '')
        if chat_type_str == 'group':
            chat_type = ChatType.GROUP
        elif chat_type_str == 'p2p':
            chat_type = ChatType.PRIVATE
        else:
            chat_type = ChatType.UNKNOWN
        
        # 创建时间
        create_time = header.get('create_time', '')
        try:
            timestamp = datetime.fromtimestamp(int(create_time) / 1000)
        except (ValueError, TypeError):
            timestamp = datetime.now()
        
        return BotMessage(
            platform=self.platform_name,
            message_id=message_data.get('message_id', ''),
            user_id=user_id,
            user_name=sender_id.get('user_id', user_id),  # 飞书不直接返回用户名
            chat_id=message_data.get('chat_id', ''),
            chat_type=chat_type,
            content=content,
            raw_content=raw_content,
            mentioned=mentioned,
            mentions=[m.get('key', '') for m in message_data.get('mentions', [])],
            timestamp=timestamp,
            raw_data=data,
        )
    
    def _extract_command(self, text: str, event: Dict) -> str:
        """
        提取命令内容（去除 @机器人）
        
        飞书的 @用户 格式是：@_user_1
        """
        # 移除 @提及
        mentions = event.get('message', {}).get('mentions', [])
        for mention in mentions:
            key = mention.get('key', '')
            if key:
                text = text.replace(key, '')
        
        # 清理多余空格
        return ' '.join(text.split())
    
    def format_response(
        self, 
        response: BotResponse, 
        message: BotMessage
    ) -> WebhookResponse:
        """
        格式化飞书响应
        
        飞书 Webhook 只需要返回空响应，实际回复需要调用 API。
        这里我们返回空响应，然后通过 NotificationService 发送消息。
        """
        # 飞书事件回调只需要返回空 200 响应
        # 实际的消息回复需要调用飞书 API（通过 NotificationService）
        
        if response.text:
            # 通过通知服务发送响应
            self._send_reply(response, message)
        
        return WebhookResponse.success()
    
    def _send_reply(self, response: BotResponse, message: BotMessage) -> None:
        """
        发送回复消息
        
        通过飞书 API 发送回复（异步，不阻塞 Webhook 响应）
        """
        import threading
        
        def _send():
            try:
                from notification import NotificationService
                
                notifier = NotificationService()
                
                # 构建回复内容
                text = response.text
                if response.at_user and message.user_id:
                    # 飞书的 @用户 需要使用 open_id
                    text = f"<at user_id=\"{message.user_id}\"></at> {text}"
                
                # 发送到飞书
                notifier.send_to_feishu(text)
                
            except Exception as e:
                logger.error(f"[Feishu] 发送回复失败: {e}")
        
        # 异步发送
        thread = threading.Thread(target=_send, daemon=True)
        thread.start()
