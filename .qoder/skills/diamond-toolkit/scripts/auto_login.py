#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
自动登录工具（基于 Chrome DevTools Protocol）
使用 Playwright 控制浏览器，自动提取并保存登录凭证
"""

import os
import sys
import json
import re
import asyncio
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, Dict, Any, List
from urllib.parse import urlparse

try:
    from playwright.async_api import async_playwright, Page, Response
    from cryptography.fernet import Fernet
    import hashlib
except ImportError as e:
    print(f"错误: 缺少依赖库 - {e}")
    print("请运行以下命令安装依赖:")
    print("  pip install playwright cryptography")
    print("  playwright install chromium")
    sys.exit(1)


class CredentialManager:
    """凭证管理器 - 负责加密存储和读取凭证"""
    
    def __init__(self, storage_dir: Optional[str] = None):
        """
        初始化凭证管理器
        
        Args:
            storage_dir: 凭证存储目录，默认为 ~/.auto-login-credentials
        """
        if storage_dir is None:
            storage_dir = os.path.expanduser("~/.auto-login-credentials")
        
        self.storage_dir = Path(storage_dir)
        self.storage_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        
        # 生成加密密钥（基于机器标识）
        self.cipher = self._get_cipher()
    
    def _get_cipher(self) -> Fernet:
        """生成加密密钥"""
        # 使用机器标识生成密钥
        machine_id = self._get_machine_id()
        key = hashlib.sha256(machine_id.encode()).digest()
        # Fernet 需要 base64 编码的 32 字节密钥
        import base64
        key_b64 = base64.urlsafe_b64encode(key)
        return Fernet(key_b64)
    
    def _get_machine_id(self) -> str:
        """
        获取机器唯一标识
        
        尝试多种方式获取机器唯一标识，按优先级：
        1. macOS: IOPlatformUUID
        2. Linux: /etc/machine-id 或 /var/lib/dbus/machine-id
        3. Windows: MachineGuid 注册表
        4. 降级方案: hostname + MAC 地址
        """
        import platform
        import uuid
        
        system = platform.system()
        
        try:
            # macOS
            if system == "Darwin":
                import subprocess
                result = subprocess.run(
                    ["ioreg", "-rd1", "-c", "IOPlatformExpertDevice"],
                    capture_output=True,
                    text=True,
                    timeout=5
                )
                if result.returncode == 0:
                    for line in result.stdout.split('\n'):
                        if 'IOPlatformUUID' in line:
                            # 提取 UUID
                            parts = line.split('"')
                            if len(parts) >= 4:
                                return parts[3]
            
            # Linux
            elif system == "Linux":
                # 尝试读取 /etc/machine-id
                machine_id_paths = [
                    "/etc/machine-id",
                    "/var/lib/dbus/machine-id"
                ]
                for path in machine_id_paths:
                    try:
                        with open(path, 'r') as f:
                            machine_id = f.read().strip()
                            if machine_id:
                                return machine_id
                    except (FileNotFoundError, PermissionError):
                        continue
            
            # Windows
            elif system == "Windows":
                import winreg
                try:
                    key = winreg.OpenKey(
                        winreg.HKEY_LOCAL_MACHINE,
                        r"SOFTWARE\Microsoft\Cryptography",
                        0,
                        winreg.KEY_READ | winreg.KEY_WOW64_64KEY
                    )
                    machine_guid, _ = winreg.QueryValueEx(key, "MachineGuid")
                    winreg.CloseKey(key)
                    if machine_guid:
                        return machine_guid
                except Exception:
                    pass
        
        except Exception:
            pass
        
        # 降级方案：使用 hostname + MAC 地址
        try:
            # 获取第一个非本地回环的 MAC 地址
            mac = uuid.getnode()
            hostname = platform.node()
            return f"{hostname}-{mac:012x}"
        except Exception:
            # 最后的降级方案
            return f"{platform.node()}-{platform.machine()}-{uuid.uuid4().hex[:8]}"
    
    def save_credentials(self, site: str, credentials: Dict[str, Any]) -> None:
        """
        保存凭证到文件
        
        Args:
            site: 站点标识（如域名）
            credentials: 凭证数据
        """
        file_path = self.storage_dir / f"{site}.json"
        
        # 添加元数据
        credentials["saved_at"] = datetime.now().isoformat()
        
        # 序列化并加密
        data = json.dumps(credentials, ensure_ascii=False)
        encrypted = self.cipher.encrypt(data.encode())
        
        # 写入文件（权限 600）
        file_path.write_bytes(encrypted)
        os.chmod(file_path, 0o600)
        
        print(f"✓ 凭证已保存到: {file_path}")
    
    def load_credentials(self, site: str) -> Optional[Dict[str, Any]]:
        """
        从文件加载凭证
        
        Args:
            site: 站点标识
            
        Returns:
            凭证数据，如果不存在则返回 None
        """
        file_path = self.storage_dir / f"{site}.json"
        
        if not file_path.exists():
            return None
        
        try:
            # 读取并解密
            encrypted = file_path.read_bytes()
            data = self.cipher.decrypt(encrypted).decode()
            credentials = json.loads(data)
            
            return credentials
        except Exception as e:
            print(f"✗ 加载凭证失败: {e}")
            return None
    
    def is_expired(self, credentials: Dict[str, Any]) -> bool:
        """
        检查凭证是否过期
        
        Args:
            credentials: 凭证数据
            
        Returns:
            True 如果已过期，否则 False
        """
        if "expires_at" not in credentials:
            return False
        
        try:
            expires_at = datetime.fromisoformat(credentials["expires_at"])
            return datetime.now() >= expires_at
        except Exception:
            return False


class AutoLogin:
    """自动登录客户端"""
    
    def __init__(
        self,
        login_url: str,
        callback_pattern: str,
        credential_type: str = "cookie",
        storage_path: Optional[str] = None,
        headless: bool = False,
        timeout: int = 120,
        debug: bool = False
    ):
        """
        初始化自动登录客户端
        
        Args:
            login_url: 登录页面 URL
            callback_pattern: 登录回调 URL 匹配模式（正则表达式）
            credential_type: 凭证类型 (cookie/token/both)
            storage_path: 凭证存储路径
            headless: 是否使用无头模式
            timeout: 登录超时时间（秒）
            debug: 是否启用调试模式
        """
        self.login_url = login_url
        self.callback_pattern = re.compile(callback_pattern)
        self.credential_type = credential_type
        self.headless = headless
        self.timeout = timeout * 1000  # 转换为毫秒
        self.debug = debug
        
        # 从 URL 提取站点标识
        parsed = urlparse(login_url)
        self.site = parsed.netloc
        
        # 初始化凭证管理器
        self.credential_manager = CredentialManager(storage_path)
        
        # 存储提取的凭证
        self.extracted_credentials: Optional[Dict[str, Any]] = None
    
    def _log(self, message: str) -> None:
        """输出日志"""
        if self.debug:
            timestamp = datetime.now().strftime("%H:%M:%S")
            print(f"[{timestamp}] {message}")
    
    async def _handle_response(self, response: Response) -> None:
        """
        处理网络响应，提取凭证
        
        Args:
            response: Playwright Response 对象
        """
        url = response.url
        
        # 检查是否匹配回调 URL
        if not self.callback_pattern.search(url):
            return
        
        self._log(f"匹配到登录回调: {url}")
        
        credentials: Dict[str, Any] = {
            "site": self.site,
            "credential_type": self.credential_type,
            "created_at": datetime.now().isoformat()
        }
        
        # 提取 Cookie
        if self.credential_type in ["cookie", "both"]:
            # 注意：这里需要从 page.context 获取 cookies
            # 在回调中无法直接访问，需要在外部处理
            credentials["cookies"] = []
        
        # 提取 Token
        if self.credential_type in ["token", "both"]:
            try:
                # 从响应头提取 Token
                headers = response.headers
                if "authorization" in headers:
                    credentials["token"] = {
                        "access_token": headers["authorization"].replace("Bearer ", ""),
                        "token_type": "Bearer"
                    }
                
                # 从响应体提取 Token
                if response.ok:
                    try:
                        body = await response.json()
                        if "access_token" in body:
                            credentials["token"] = body
                    except Exception:
                        pass
            except Exception as e:
                self._log(f"提取 Token 失败: {e}")
        
        self.extracted_credentials = credentials
        self._log("✓ 凭证提取成功")
    
    async def login(self) -> Dict[str, Any]:
        """
        执行自动登录流程
        
        Returns:
            提取的凭证数据
        """
        print("=" * 60)
        print("自动登录工具（基于 CDP）")
        print("=" * 60)
        print(f"站点: {self.site}")
        print(f"登录 URL: {self.login_url}")
        print(f"凭证类型: {self.credential_type}")
        print(f"无头模式: {self.headless}")
        print("-" * 60)
        
        async with async_playwright() as p:
            # 启动浏览器
            self._log("正在启动浏览器...")
            browser = await p.chromium.launch(
                headless=self.headless,
                args=['--no-sandbox']
            )
            
            # 创建浏览器上下文（持久化 Cookie）
            context = await browser.new_context()
            
            # 创建页面
            page = await context.new_page()
            
            # 监听网络响应
            page.on("response", self._handle_response)
            
            try:
                # 导航到登录页面
                print("正在打开登录页面...")
                await page.goto(self.login_url, timeout=self.timeout)
                
                if not self.headless:
                    print("\n请在浏览器中完成登录操作...")
                    print("登录成功后，工具将自动提取凭证\n")
                
                # 等待登录回调
                print("等待登录回调...")
                start_time = datetime.now()
                
                while self.extracted_credentials is None:
                    await asyncio.sleep(0.5)
                    
                    # 检查超时
                    if (datetime.now() - start_time).total_seconds() > self.timeout / 1000:
                        raise TimeoutError("登录超时")
                
                # 提取 Cookie（如果需要）
                if self.credential_type in ["cookie", "both"]:
                    cookies = await context.cookies()
                    self.extracted_credentials["cookies"] = [
                        {
                            "name": c["name"],
                            "value": c["value"],
                            "domain": c["domain"],
                            "path": c["path"],
                            "expires": c.get("expires", -1)
                        }
                        for c in cookies
                    ]
                    
                    # 计算过期时间（取最早过期的 Cookie）
                    expires_list = [
                        c.get("expires", -1) 
                        for c in cookies 
                        if c.get("expires", -1) > 0
                    ]
                    if expires_list:
                        min_expires = min(expires_list)
                        self.extracted_credentials["expires_at"] = datetime.fromtimestamp(
                            min_expires
                        ).isoformat()
                
                # 保存凭证
                self.credential_manager.save_credentials(
                    self.site,
                    self.extracted_credentials
                )
                
                print("-" * 60)
                print("✓ 登录成功！凭证已保存")
                print("=" * 60)
                
                return self.extracted_credentials
                
            except TimeoutError:
                print("✗ 登录超时，请检查网络连接或增加 timeout 参数")
                raise
            except Exception as e:
                print(f"✗ 登录失败: {e}")
                raise
            finally:
                await browser.close()
    
    def get_credentials(self, auto_refresh: bool = True) -> Optional[Dict[str, Any]]:
        """
        获取凭证（同步方法）
        
        Args:
            auto_refresh: 是否自动刷新过期凭证
            
        Returns:
            凭证数据
        """
        # 尝试加载已保存的凭证
        credentials = self.credential_manager.load_credentials(self.site)
        
        if credentials is None:
            print(f"未找到 {self.site} 的凭证，需要重新登录")
            return None
        
        # 检查是否过期
        if self.credential_manager.is_expired(credentials):
            print(f"{self.site} 的凭证已过期")
            
            if auto_refresh:
                print("正在自动刷新凭证...")
                # 异步登录需要在事件循环中运行
                credentials = asyncio.run(self.login())
            else:
                return None
        
        return credentials


def main():
    """主函数"""
    import argparse
    
    parser = argparse.ArgumentParser(
        description="自动登录工具（基于 CDP）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 登录 MSE 配置中心
  %(prog)s https://mse.alibaba-inc.com "/api/user/info"
  
  # 提取 Token
  %(prog)s https://api.example.com/login "/oauth/token" --type token
  
  # 无头模式（需要已有凭证）
  %(prog)s https://example.com "/callback" --headless
        """
    )
    
    parser.add_argument("login_url", help="登录页面 URL")
    parser.add_argument("callback_pattern", help="登录回调 URL 匹配模式（正则表达式）")
    parser.add_argument(
        "--type",
        choices=["cookie", "token", "both"],
        default="cookie",
        help="凭证类型（默认: cookie）"
    )
    parser.add_argument(
        "--storage",
        help="凭证存储路径（默认: ~/.auto-login-credentials）"
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        help="使用无头模式"
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=120,
        help="登录超时时间（秒，默认: 120）"
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="启用调试模式"
    )
    
    args = parser.parse_args()
    
    # 创建登录客户端
    client = AutoLogin(
        login_url=args.login_url,
        callback_pattern=args.callback_pattern,
        credential_type=args.type,
        storage_path=args.storage,
        headless=args.headless,
        timeout=args.timeout,
        debug=args.debug
    )
    
    # 执行登录
    try:
        asyncio.run(client.login())
        sys.exit(0)
    except Exception as e:
        print(f"\n登录失败: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
