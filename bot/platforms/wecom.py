# -*- coding: utf-8 -*-
"""
===================================
企业微信平台适配器
===================================

处理企业微信机器人的回调消息。

企业微信机器人文档：
https://developer.work.weixin.qq.com/document/path/91770
"""

import hashlib
import logging
import time
import xml.etree.ElementTree as ET
from datetime import datetime
from typing import Dict, Any, Optional

from bot.platforms.base import BotPlatform
from bot.models import BotMessage, BotResponse, WebhookResponse, ChatType

logger = logging.getLogger(__name__)


class WecomPlatform(BotPlatform):
    """
    企业微信平台适配器
    
    支持：
    - 应用消息回调
    - URL 验证
    - 消息加解密
    
    配置要求：
    - WECOM_CORPID: 企业 ID
    - WECOM_TOKEN: 回调 Token
    - WECOM_ENCODING_AES_KEY: 消息加解密密钥
    - WECOM_AGENT_ID: 应用 AgentId
    
    注意：企业微信消息回调需要在企业微信管理后台配置回调 URL
    """
    
    def __init__(self):
        from config import get_config
        config = get_config()
        
        self._corpid = getattr(config, 'wecom_corpid', None)
        self._token = getattr(config, 'wecom_token', None)
        self._encoding_aes_key = getattr(config, 'wecom_encoding_aes_key', None)
        self._agent_id = getattr(config, 'wecom_agent_id', None)
        
        # 初始化加解密器（如果配置了密钥）
        self._crypto = None
        if self._corpid and self._token and self._encoding_aes_key:
            try:
                self._init_crypto()
            except Exception as e:
                logger.warning(f"[WeCom] 加解密器初始化失败: {e}")
    
    def _init_crypto(self):
        """初始化消息加解密器"""
        # 企业微信消息加解密需要额外的库
        # 这里提供一个简化的实现框架
        pass
    
    @property
    def platform_name(self) -> str:
        return "wecom"
    
    def verify_request(self, headers: Dict[str, str], body: bytes) -> bool:
        """
        验证企业微信请求签名
        
        签名算法：
        1. 将 token, timestamp, nonce, msg_encrypt 排序后拼接
        2. SHA1 加密
        3. 比对签名
        """
        if not self._token:
            logger.warning("[WeCom] 未配置 token，跳过签名验证")
            return True
        
        # 从 URL 参数获取（需要在路由处理中传递）
        # 这里假设参数已经放在 headers 中（实际实现需要从 URL 获取）
        msg_signature = headers.get('msg_signature', '')
        timestamp = headers.get('timestamp', '')
        nonce = headers.get('nonce', '')
        
        if not all([msg_signature, timestamp, nonce]):
            logger.debug("[WeCom] 缺少签名参数")
            return True  # 可能是其他类型的请求
        
        # 解析 XML 获取加密消息
        try:
            root = ET.fromstring(body.decode('utf-8'))
            encrypt = root.find('Encrypt')
            msg_encrypt = encrypt.text if encrypt is not None else ''
        except Exception as e:
            logger.warning(f"[WeCom] 解析 XML 失败: {e}")
            return False
        
        # 计算签名
        sort_list = sorted([self._token, timestamp, nonce, msg_encrypt])
        sign_str = ''.join(sort_list)
        expected_signature = hashlib.sha1(sign_str.encode('utf-8')).hexdigest()
        
        if msg_signature != expected_signature:
            logger.warning("[WeCom] 签名验证失败")
            return False
        
        return True
    
    def handle_challenge(self, data: Dict[str, Any]) -> Optional[WebhookResponse]:
        """
        处理企业微信 URL 验证请求
        
        企业微信验证时会发送 GET 请求，包含 echostr 参数。
        需要解密 echostr 后返回。
        
        注意：这个方法处理的是已解析的数据，
        实际的 URL 验证在路由层处理（因为是 GET 请求）。
        """
        # 企业微信的 URL 验证是 GET 请求，不会到达这里
        # 这里只是占位实现
        return None
    
    def parse_message(self, data: Dict[str, Any]) -> Optional[BotMessage]:
        """
        解析企业微信消息
        
        企业微信消息格式（解密后的 XML）：
        <xml>
            <ToUserName><![CDATA[xxx]]></ToUserName>
            <FromUserName><![CDATA[userid]]></FromUserName>
            <CreateTime>1234567890</CreateTime>
            <MsgType><![CDATA[text]]></MsgType>
            <Content><![CDATA[@机器人 /analyze 600519]]></Content>
            <MsgId>xxx</MsgId>
            <AgentID>1000002</AgentID>
        </xml>
        
        这里的 data 是已经解析好的字典格式。
        """
        # 检查消息类型
        msg_type = data.get('MsgType', '')
        if msg_type != 'text':
            logger.debug(f"[WeCom] 忽略非文本消息: {msg_type}")
            return None
        
        # 获取消息内容
        raw_content = data.get('Content', '')
        
        # 提取命令
        content = self._extract_command(raw_content)
        
        # 企业微信的 @提及 格式
        mentioned = '@' in raw_content
        
        # 创建时间
        create_time = data.get('CreateTime', '')
        try:
            timestamp = datetime.fromtimestamp(int(create_time))
        except (ValueError, TypeError):
            timestamp = datetime.now()
        
        return BotMessage(
            platform=self.platform_name,
            message_id=data.get('MsgId', ''),
            user_id=data.get('FromUserName', ''),
            user_name=data.get('FromUserName', ''),  # 企业微信返回的是 userid
            chat_id=data.get('ToUserName', ''),
            chat_type=ChatType.UNKNOWN,  # 企业微信需要额外判断
            content=content,
            raw_content=raw_content,
            mentioned=mentioned,
            mentions=[],
            timestamp=timestamp,
            raw_data=data,
        )
    
    def _extract_command(self, text: str) -> str:
        """提取命令内容"""
        import re
        # 移除 @提及
        text = re.sub(r'@[\S]+\s*', '', text.strip())
        return text.strip()
    
    def format_response(
        self, 
        response: BotResponse, 
        message: BotMessage
    ) -> WebhookResponse:
        """
        格式化企业微信响应
        
        企业微信被动回复消息格式（XML）：
        <xml>
            <ToUserName><![CDATA[userid]]></ToUserName>
            <FromUserName><![CDATA[corpid]]></FromUserName>
            <CreateTime>1234567890</CreateTime>
            <MsgType><![CDATA[text]]></MsgType>
            <Content><![CDATA[回复内容]]></Content>
        </xml>
        
        注意：由于需要返回 XML 格式，这里的处理与其他平台不同。
        """
        if not response.text:
            return WebhookResponse.success()
        
        # 企业微信的被动回复需要返回 XML 格式
        # 但我们的 WebhookResponse 是 JSON 格式的
        # 这里我们通过通知服务主动发送消息
        self._send_reply(response, message)
        
        return WebhookResponse.success()
    
    def _send_reply(self, response: BotResponse, message: BotMessage) -> None:
        """
        发送回复消息
        
        通过企业微信 Webhook 发送回复
        """
        import threading
        
        def _send():
            try:
                from notification import NotificationService
                
                notifier = NotificationService()
                
                # 发送到企业微信
                notifier.send_to_wechat(response.text)
                
            except Exception as e:
                logger.error(f"[WeCom] 发送回复失败: {e}")
        
        # 异步发送
        thread = threading.Thread(target=_send, daemon=True)
        thread.start()
    
    def decrypt_message(self, encrypt_msg: str) -> str:
        """
        解密企业微信消息
        
        这里提供简化的框架，实际实现需要：
        1. Base64 解码
        2. AES 解密
        3. 去除填充
        4. 校验 corpid
        
        Args:
            encrypt_msg: 加密的消息内容
            
        Returns:
            解密后的 XML 字符串
        """
        if not self._encoding_aes_key:
            logger.warning("[WeCom] 未配置加密密钥，无法解密")
            return encrypt_msg
        
        # 实际的解密实现需要引入加密库
        # 这里返回原始内容作为占位
        logger.warning("[WeCom] 消息解密功能需要完整实现")
        return encrypt_msg
    
    def encrypt_message(self, reply_msg: str) -> str:
        """
        加密回复消息
        
        Args:
            reply_msg: 回复的 XML 字符串
            
        Returns:
            加密后的消息
        """
        if not self._encoding_aes_key:
            return reply_msg
        
        # 实际的加密实现需要引入加密库
        logger.warning("[WeCom] 消息加密功能需要完整实现")
        return reply_msg
    
    @staticmethod
    def parse_xml_to_dict(xml_str: str) -> Dict[str, str]:
        """
        将 XML 字符串解析为字典
        
        Args:
            xml_str: XML 字符串
            
        Returns:
            解析后的字典
        """
        try:
            root = ET.fromstring(xml_str)
            return {child.tag: child.text or '' for child in root}
        except ET.ParseError as e:
            logger.error(f"[WeCom] XML 解析失败: {e}")
            return {}
