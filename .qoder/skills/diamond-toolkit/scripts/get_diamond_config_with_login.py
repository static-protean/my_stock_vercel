#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Diamond 配置获取脚本（集成自动登录）
使用 Playwright 直接访问 MSE 配置中心，自动处理登录
"""

import sys
import os
import json
import asyncio
import urllib.parse
from typing import Optional, Dict, Any
from pathlib import Path

# 确保可以导入同目录下的 auto_login 模块
CURRENT_DIR = Path(__file__).parent
sys.path.insert(0, str(CURRENT_DIR))

try:
    from playwright.async_api import async_playwright, Page, TimeoutError as PlaywrightTimeout
    from auto_login import AutoLogin, CredentialManager
except ImportError as e:
    print(f"错误: 缺少依赖库 - {e}")
    print("请运行以下命令安装依赖:")
    print("  pip install playwright cryptography")
    print("  playwright install chromium")
    sys.exit(1)


class DiamondConfigFetcherWithLogin:
    """Diamond 配置获取器（集成自动登录，避免 CSS 截断）"""
    
    def __init__(self, env: str = "pre", headless: bool = False, debug: bool = False):
        """
        初始化 Diamond 配置获取器
        
        Args:
            env: 环境标识，默认为 pre（预发环境）
            headless: 是否使用无头模式
            debug: 是否启用调试模式
        """
        self.env = env
        self.base_url = "https://mse.alibaba-inc.com"
        self.headless = headless
        self.debug = debug
        
        # 初始化凭证管理器
        self.credential_manager = CredentialManager()
    
    def _log(self, message: str) -> None:
        """输出日志"""
        if self.debug:
            from datetime import datetime
            timestamp = datetime.now().strftime("%H:%M:%S")
            print(f"[{timestamp}] {message}")
    
    def _build_url(
        self, 
        data_id: str, 
        group_id: str,
        namespace_id: str
    ) -> str:
        """
        构建 Diamond 配置查询 URL
        
        Args:
            data_id: 配置的 dataId
            group_id: 配置的 groupId
            namespace_id: 命名空间ID
            
        Returns:
            完整的查询 URL
        """
        params = {
            'dataId': data_id,
            'group': group_id,
            'namespaceId': namespace_id,
            'tab': 'content'
        }
        
        query_string = urllib.parse.urlencode(params)
        url = f"{self.base_url}/{self.env}/diamond/configlist/configdetail?{query_string}"
        
        return url
    
    async def _check_login_required(self, page: Page, _retries: int = 0) -> bool:
        """
        检查页面是否需要登录

        Args:
            page: Playwright Page 对象
            _retries: 内部重试计数，防止无限循环

        Returns:
            True 如果需要登录，否则 False
        """
        MAX_RETRIES = 3
        try:
            # 等待页面加载
            await asyncio.sleep(2)

            # 获取当前 URL 和标题
            current_url = page.url
            title = await page.title()

            self._log(f"当前 URL: {current_url}")
            self._log(f"页面标题: {title}")

            # 1. 检查 URL 是否包含登录关键词
            # 注意：SSO_TICKET 是登录成功后的票据，不应判断为需要登录
            if ('login' in current_url.lower() or 'sso' in current_url.lower() or 'auth' in current_url.lower()) and 'SSO_TICKET' not in current_url:
                self._log("URL 包含登录关键词，需要登录")
                return True

            # 如果 URL 包含 SSO_TICKET，说明已经登录成功
            if 'SSO_TICKET' in current_url:
                self._log("URL 包含 SSO_TICKET，已登录成功")
                return False

            # 2. 检查页面标题是否包含登录关键词
            if '登录' in title or 'login' in title.lower() or '认证' in title:
                self._log("页面标题包含登录关键词，需要登录")
                return True

            # 3. 检查是否有明确的登录表单元素
            login_selectors = [
                'input[type="password"]',
                'button[type="submit"]',
                '.login-form',
                '#login',
                '[class*="login"]',
                'img[alt*="二维码"]',
                'img[alt*="扫码"]'
            ]

            for selector in login_selectors:
                try:
                    element = await page.query_selector(selector)
                    if element:
                        is_visible = await element.is_visible()
                        if is_visible:
                            self._log(f"检测到登录元素: {selector}")
                            return True
                except Exception:
                    continue

            # 4. 检查是否在 MSE 配置详情页面（通过 URL 特征）
            if 'mse.alibaba-inc.com' in current_url and 'configdetail' in current_url:
                self._log("已在 MSE 配置详情页面，无需登录")
                return False

            # 5. 检查是否有 MSE 页面的特征元素
            mse_selectors = [
                '.ant-layout',
                '.ant-menu',
                '[class*="config"]',
                'textarea',
                '.ace_editor'
            ]

            for selector in mse_selectors:
                try:
                    element = await page.query_selector(selector)
                    if element:
                        self._log(f"检测到 MSE 页面元素: {selector}，无需登录")
                        return False
                except Exception:
                    continue

            # 6. URL 变化时重试（有上限，防止无限循环）
            if _retries < MAX_RETRIES:
                self._log("等待页面完全加载...")
                await asyncio.sleep(3)
                new_url = page.url
                if new_url != current_url:
                    self._log(f"URL 已变化: {new_url}（重试 {_retries + 1}/{MAX_RETRIES}）")
                    return await self._check_login_required(page, _retries + 1)

            # 默认认为不需要登录（避免误判）
            self._log("无法明确判断，默认认为不需要登录")
            return False

        except Exception as e:
            self._log(f"检查登录状态时出错: {e}")
            return False
    
    async def _try_api_fetch(self, page: Page, data_id: str, group_id: str, namespace_id: str) -> Optional[str]:
        """
        尝试通过 API 直接获取配置内容（最快，避免页面渲染）
        
        Args:
            page: Playwright Page 对象
            data_id: 配置的 dataId
            group_id: 配置的 groupId
            namespace_id: 命名空间ID
            
        Returns:
            配置内容字符串，如果失败则返回 None
        """
        try:
            self._log("尝试使用 API 直接获取配置...")
            
            # MSE Diamond 配置中心的 API 接口
            api_url = f"{self.base_url}/{self.env}/diamond/api/config/getConfig"
            
            # 使用 page.evaluate 发起 fetch 请求，可以复用页面的登录态
            result = await page.evaluate("""
                async ({ apiUrl, dataId, groupId, namespaceId }) => {
                    try {
                        const params = new URLSearchParams({
                            dataId: dataId,
                            group: groupId,
                            namespaceId: namespaceId
                        });
                        
                        const response = await fetch(apiUrl + '?' + params.toString(), {
                            method: 'GET',
                            credentials: 'include',
                            headers: {
                                'Accept': 'application/json',
                            }
                        });
                        
                        if (!response.ok) {
                            return { success: false, error: `HTTP ${response.status}` };
                        }
                        
                        const data = await response.json();
                        
                        // MSE Diamond API 返回格式可能是: { success: true, data: { content: "..." } }
                        if (data && data.data && data.data.content) {
                            return { success: true, content: data.data.content };
                        }
                        // 或者直接返回 content
                        if (data && data.content) {
                            return { success: true, content: data.content };
                        }
                        
                        return { success: false, error: 'API 返回格式不符合预期' };
                    } catch (error) {
                        return { success: false, error: error.toString() };
                    }
                }
            """, {
                "apiUrl": api_url,
                "dataId": data_id,
                "groupId": group_id,
                "namespaceId": namespace_id
            })
            
            if result.get("success"):
                self._log("✓ API 获取成功")
                return result.get("content")
            else:
                self._log(f"API 获取失败: {result.get('error')}")
                return None
                
        except Exception as e:
            self._log(f"API 获取异常: {e}")
            return None
    
    async def _try_console_clipboard_extract(self, page: Page) -> Optional[str]:
        """
        尝试从控制台的格式化视图提取配置内容（通过剪贴板）
        这是最可靠的方式，因为可以完整获取大配置内容
        
        步骤：
        1. 查找 formatConfigSize 相关的配置展示区域
        2. 循环点击"显示更多"按钮，直到全部展开
        3. 点击"复制"按钮，将内容复制到剪贴板
        4. 从剪贴板读取内容
        
        Args:
            page: Playwright Page 对象
            
        Returns:
            配置内容字符串，如果失败则返回 None
        """
        try:
            self._log("尝试从控制台格式化视图提取配置...")
            
            # 等待页面完全加载
            await asyncio.sleep(2)
            
            # 使用 JavaScript 查找并展开所有内容
            result = await page.evaluate("""
                async () => {
                    // 1. 循环点击"显示更多"按钮，直到没有为止
                    let clickCount = 0;
                    const maxClicks = 100; // 防止无限循环
                    
                    while (clickCount < maxClicks) {
                        // 查找"显示更多"按钮
                        const showMoreButtons = Array.from(document.querySelectorAll('button, a, span'))
                            .filter(el => {
                                const text = el.textContent || el.innerText || '';
                                return text.includes('显示更多') || text.includes('展开') || text.includes('查看更多');
                            });
                        
                        if (showMoreButtons.length === 0) {
                            break; // 没有"显示更多"按钮了
                        }
                        
                        // 点击第一个找到的按钮
                        showMoreButtons[0].click();
                        clickCount++;
                        
                        // 等待内容加载
                        await new Promise(resolve => setTimeout(resolve, 300));
                    }
                    
                    // 2. 查找"复制"按钮
                    const copyButtons = Array.from(document.querySelectorAll('button, a, span'))
                        .filter(el => {
                            const text = el.textContent || el.innerText || '';
                            return text.includes('复制') && !text.includes('复制成功');
                        });
                    
                    if (copyButtons.length === 0) {
                        return { success: false, error: '未找到复制按钮' };
                    }
                    
                    // 3. 点击复制按钮
                    copyButtons[0].click();
                    
                    // 等待复制完成
                    await new Promise(resolve => setTimeout(resolve, 500));
                    
                    // 4. 尝试从剪贴板读取（需要权限）
                    try {
                        const clipboardText = await navigator.clipboard.readText();
                        if (clipboardText && clipboardText.length > 0) {
                            return { success: true, content: clipboardText, method: 'clipboard' };
                        }
                    } catch (e) {
                        // 剪贴板读取失败，尝试其他方法
                    }
                    
                    // 5. 备选：从 Monaco Editor 获取
                    if (window.monaco && window.monaco.editor) {
                        try {
                            const models = window.monaco.editor.getModels();
                            if (models && models.length > 0) {
                                const editorContent = models[0].getValue();
                                if (editorContent && editorContent.trim().length > 0) {
                                    return { success: true, content: editorContent, method: 'monaco' };
                                }
                            }
                        } catch (e) { /* ignore */ }
                    }

                    // 6. 备选：从 textarea 获取
                    const textareas = document.querySelectorAll('textarea');
                    for (let ta of textareas) {
                        const val = ta.value || ta.textContent || '';
                        const trimmed = val.trim();
                        if (trimmed.length > 10 && !trimmed.includes('.monaco-list')) {
                            return { success: true, content: trimmed, method: 'textarea' };
                        }
                    }

                    // 7. 备选：从 pre/code 元素获取（跳过 CSS 内容）
                    const codeElements = document.querySelectorAll('pre, code');
                    for (let el of codeElements) {
                        const text = (el.textContent || '').trim();
                        if (text.length > 10 && !text.includes('.monaco-list') && !text.includes('background-color')) {
                            if (text.startsWith('{') || text.startsWith('[') || text.startsWith('<')) {
                                return { success: true, content: text, method: 'code-element' };
                            }
                        }
                    }
                    
                    return { success: false, error: '无法从剪贴板或页面提取内容' };
                }
            """)
            
            if result.get("success"):
                self._log(f"✓ 从控制台格式化视图提取成功（方法: {result.get('method')}）")
                return result.get("content")
            else:
                self._log(f"控制台格式化视图提取失败: {result.get('error')}")
                return None
                
        except Exception as e:
            self._log(f"控制台格式化视图提取异常: {e}")
            return None
    
    async def _extract_config_content(self, page: Page, data_id: str = None, group_id: str = None, namespace_id: str = None) -> Optional[str]:
        """
        从页面提取 Diamond 配置内容（避免 CSS 截断）
        优化策略：
        1. 优先尝试 API 直接获取（最快，无需页面渲染）
        2. 尝试从控制台格式化视图提取（通过剪贴板，最可靠）
        3. 使用 JavaScript 直接从编辑器获取（避免 CSS 截断）
        4. 备选方案：从 DOM 元素提取
        
        Args:
            page: Playwright Page 对象
            data_id: 配置的 dataId（用于 API 获取）
            group_id: 配置的 groupId（用于 API 获取）
            namespace_id: 命名空间ID（用于 API 获取）
            
        Returns:
            配置内容字符串，如果提取失败则返回 None
        """
        try:
            # 策略1: 优先尝试 API 获取（最快，避免大内容渲染问题）
            if data_id and group_id and namespace_id:
                api_content = await self._try_api_fetch(page, data_id, group_id, namespace_id)
                if api_content:
                    return api_content
                self._log("API 获取失败，回退到页面提取方式")
            
            # 策略2: 尝试从控制台格式化视图提取（最可靠，支持大配置）
            console_content = await self._try_console_clipboard_extract(page)
            if console_content:
                return console_content
            self._log("控制台格式化视图提取失败，继续尝试其他方式")
            
            # 等待页面加载完成，给 React 应用更多时间渲染
            self._log("等待页面完全加载...")
            await asyncio.sleep(2)  # 从 3 秒缩短到 2 秒
            
            # 仅在调试模式下保存调试信息
            if self.debug:
                screenshot_path = "/tmp/diamond_page.png"
                await page.screenshot(path=screenshot_path, full_page=True)
                self._log(f"页面截图已保存到: {screenshot_path}")
                
                html_path = "/tmp/diamond_page.html"
                html_content = await page.content()
                with open(html_path, 'w', encoding='utf-8') as f:
                    f.write(html_content)
                self._log(f"页面HTML已保存到: {html_path}")
                
                text_path = "/tmp/diamond_page.txt"
                page_text = await page.evaluate("() => document.body.innerText")
                with open(text_path, 'w', encoding='utf-8') as f:
                    f.write(page_text)
                self._log(f"页面文本已保存到: {text_path}")
            
            # 策略3: 确保在"配置内容" Tab，然后使用 JavaScript 提取（避免 CSS 截断，支持大内容）
            self._log("策略3: 确保在'配置内容' Tab 并提取配置内容")
            try:
                # 先确保点击了"配置内容" Tab（如果需要）
                await page.evaluate("""
                    () => {
                        // 查找并点击"配置内容" Tab（如果还没激活）
                        const tabs = document.querySelectorAll('[role="tab"], .ant-tabs-tab, [class*="tab"]');
                        for (let tab of tabs) {
                            const text = tab.textContent || tab.innerText;
                            if (text && text.includes('配置内容')) {
                                // 检查是否已经激活
                                const isActive = tab.classList.contains('active') || 
                                               tab.classList.contains('ant-tabs-tab-active') ||
                                               tab.getAttribute('aria-selected') === 'true';
                                if (!isActive) {
                                    tab.click();
                                    return true;
                                }
                                return false;
                            }
                        }
                        return false;
                    }
                """)
                
                # 等待 Tab 切换完成
                await asyncio.sleep(0.5)
                
                js_content = await page.evaluate("""
                    () => {
                        // 1. 优先从 Monaco Editor 获取（MSE 配置中心常用）
                        if (window.monaco && window.monaco.editor) {
                            try {
                                // Monaco Editor 可能有不同的 API
                                const models = window.monaco.editor.getModels();
                                if (models && models.length > 0) {
                                    const content = models[0].getValue();
                                    if (content && content.length > 10) {
                                        return content;
                                    }
                                }
                            } catch (e) {
                                // Monaco API 调用失败，继续尝试其他方法
                            }
                        }
                        
                        // 2. 从 Ace Editor 获取
                        if (window.ace) {
                            const editors = document.querySelectorAll('.ace_editor');
                            if (editors.length > 0) {
                                const editor = ace.edit(editors[0]);
                                if (editor) {
                                    const content = editor.getValue();
                                    if (content && content.length > 10) {
                                        return content;
                                    }
                                }
                            }
                        }
                        
                        // 3. 从所有可见的 textarea 获取（使用 value 属性，避免 CSS 截断）
                        const textareas = document.querySelectorAll('textarea');
                        for (let ta of textareas) {
                            const style = window.getComputedStyle(ta);
                            if (style.display !== 'none' && style.visibility !== 'hidden') {
                                const value = ta.value || ta.textContent;
                                if (value && value.length > 10) {
                                    return value;
                                }
                            }
                        }
                        
                        // 4. 从 pre 标签获取（使用 textContent，避免 CSS 截断）
                        const pres = document.querySelectorAll('pre');
                        for (let pre of pres) {
                            const style = window.getComputedStyle(pre);
                            if (style.display !== 'none' && style.visibility !== 'hidden') {
                                const text = pre.textContent;
                                if (text && text.length > 10) {
                                    return text.trim();
                                }
                            }
                        }
                        
                        // 5. 从 code 标签获取
                        const codes = document.querySelectorAll('code');
                        for (let code of codes) {
                            const style = window.getComputedStyle(code);
                            if (style.display !== 'none' && style.visibility !== 'hidden') {
                                const text = code.textContent;
                                if (text && text.length > 10) {
                                    return text.trim();
                                }
                            }
                        }
                        
                        // 6. 从包含配置内容的 div 获取
                        const divs = document.querySelectorAll('div[class*="content"], div[class*="config"]');
                        for (let div of divs) {
                            const text = div.textContent;
                            if (text && text.length > 20 && text.length < 100000 &&
                                (text.includes('{') || text.includes('['))) {
                                // 检查是否是纯 JSON 内容
                                const trimmed = text.trim();
                                if ((trimmed.startsWith('{') && trimmed.endsWith('}')) ||
                                    (trimmed.startsWith('[') && trimmed.endsWith(']'))) {
                                    return trimmed;
                                }
                            }
                        }
                        
                        return null;
                    }
                """)
                
                if js_content and js_content.strip():
                    self._log("使用 JavaScript 成功提取内容")
                    return js_content.strip()
            except Exception as e:
                self._log(f"JavaScript 提取失败: {e}")
            
            # 策略4: 使用选择器提取（备选方案，仍使用 textContent 避免 CSS 截断）
            self._log("策略4: 使用选择器提取内容")
            selectors = [
                'textarea.ant-input',
                'textarea[placeholder*="配置"]',
                'textarea',
                'pre.language-json',
                'pre',
                'code',
            ]
            
            for selector in selectors:
                try:
                    elements = await page.query_selector_all(selector)
                    for element in elements:
                        # 优先获取 value 属性（针对 textarea）
                        if selector.startswith('textarea'):
                            value = await element.get_attribute('value')
                            if value and len(value.strip()) > 10:
                                self._log(f"从 {selector} 的 value 属性提取内容")
                                return value.strip()
                        
                        # 获取 textContent（避免 CSS 截断）
                        content = await element.text_content()
                        if content and len(content.strip()) > 10:
                            self._log(f"使用选择器 {selector} 成功提取内容")
                            return content.strip()
                        
                        # 尝试获取 innerText（备选）
                        inner_text = await element.inner_text()
                        if inner_text and len(inner_text.strip()) > 10:
                            self._log(f"从 {selector} 的 innerText 提取内容")
                            return inner_text.strip()
                        
                except Exception as e:
                    self._log(f"选择器 {selector} 失败: {e}")
                    continue
            
            # 策略5: 从页面 HTML 中提取（最后的备选方案，仅调试模式使用）
            if self.debug:
                self._log("策略5: 从页面 HTML 中提取内容")
                page_html = await page.content()
                
                import re
                # 查找可能的配置内容
                patterns = [
                    r'<textarea[^>]*>([\s\S]*?)</textarea>',
                    r'<pre[^>]*>([\s\S]*?)</pre>',
                    r'<code[^>]*>([\s\S]*?)</code>',
                ]
                
                for pattern in patterns:
                    matches = re.findall(pattern, page_html)
                    for match in matches:
                        content = match.strip()
                        if len(content) > 10:
                            # 尝试解码 HTML 实体
                            try:
                                import html
                                content = html.unescape(content)
                            except Exception:
                                pass
                            
                            self._log(f"从 HTML 中提取到内容（长度: {len(content)}）")
                            return content
            
            return None
            
        except Exception as e:
            self._log(f"提取配置内容失败: {e}")
            return None
    
    async def _perform_login(self, page: Page, url: str) -> bool:
        """
        执行登录流程
        
        Args:
            page: Playwright Page 对象
            url: 目标 URL
            
        Returns:
            True 如果登录成功，否则 False
        """
        print("\n" + "=" * 60)
        print("检测到需要登录")
        print("=" * 60)
        print("正在准备自动登录...")
        print()
        
        # 检查是否已有保存的凭证
        site = "mse.alibaba-inc.com"
        credentials = self.credential_manager.load_credentials(site)
        
        if credentials and not self.credential_manager.is_expired(credentials):
            print("发现已保存的登录凭证，尝试使用...")
            
            # 设置 Cookie
            try:
                context = page.context
                await context.add_cookies(credentials.get("cookies", []))
                
                # 刷新页面
                await page.reload()
                await asyncio.sleep(2)
                
                # 检查是否还需要登录
                if not await self._check_login_required(page):
                    print("✓ 使用已保存的凭证登录成功")
                    return True
                else:
                    print("已保存的凭证无效，需要重新登录")
            except Exception as e:
                print(f"使用已保存凭证失败: {e}")
        
        # headless 模式下无法手动登录，直接报错
        if self.headless:
            print("\n✗ 无头模式下没有有效的登录凭证，无法自动登录")
            print("请先用非无头模式运行一次以完成登录：")
            print(f"  python3 {__file__} <dataId> <groupId>")
            print("登录凭证会被保存，之后就可以用 --headless 了")
            return False

        # 需要用户手动登录
        print("\n" + "=" * 60)
        print("需要您的授权")
        print("=" * 60)
        print()
        print("请在打开的浏览器窗口中完成登录操作：")
        print("  1. 使用钉钉扫码或输入账号密码")
        print("  2. 完成登录后，工具将自动继续")
        print("  3. 登录凭证将被保存，下次无需重复登录")
        print()
        print("等待登录中...")
        print("-" * 60)

        # 等待用户登录（最多等待 5 分钟）
        max_wait = 300  # 5 分钟
        wait_interval = 2  # 每 2 秒检查一次
        waited = 0
        
        while waited < max_wait:
            await asyncio.sleep(wait_interval)
            waited += wait_interval
            
            # 检查是否登录成功
            if not await self._check_login_required(page):
                print("\n✓ 登录成功！")
                
                # 保存登录凭证
                try:
                    context = page.context
                    cookies = await context.cookies()
                    
                    credentials = {
                        "site": site,
                        "credential_type": "cookie",
                        "cookies": [
                            {
                                "name": c["name"],
                                "value": c["value"],
                                "domain": c["domain"],
                                "path": c["path"],
                                "expires": c.get("expires", -1)
                            }
                            for c in cookies
                        ],
                        "created_at": None  # 将由 save_credentials 设置
                    }
                    
                    # 计算过期时间
                    from datetime import datetime
                    expires_list = [
                        c.get("expires", -1) 
                        for c in cookies 
                        if c.get("expires", -1) > 0
                    ]
                    if expires_list:
                        min_expires = min(expires_list)
                        credentials["expires_at"] = datetime.fromtimestamp(
                            min_expires
                        ).isoformat()
                    
                    self.credential_manager.save_credentials(site, credentials)
                    print("✓ 登录凭证已保存")
                except Exception as e:
                    print(f"保存凭证时出错: {e}")
                
                return True
            
            # 显示等待进度
            if waited % 10 == 0:
                print(f"已等待 {waited} 秒...")
        
        print("\n✗ 登录超时")
        return False
    
    def _try_cookie_api_fetch(self, data_id: str, group_id: str, namespace_id: str) -> Optional[str]:
        """
        使用保存的 cookies 直接 HTTP 请求 API 获取配置（不启动浏览器）

        Returns:
            配置内容字符串，失败返回 None
        """
        try:
            import urllib.request

            site = "mse.alibaba-inc.com"
            credentials = self.credential_manager.load_credentials(site)
            if not credentials or self.credential_manager.is_expired(credentials):
                self._log("无有效凭证，跳过 cookie API 获取")
                return None

            cookies = credentials.get("cookies", [])
            if not cookies:
                return None

            cookie_header = "; ".join(
                f"{c['name']}={c['value']}" for c in cookies
                if c.get("domain", "").endswith("alibaba-inc.com")
            )
            if not cookie_header:
                return None

            params = urllib.parse.urlencode({
                "dataId": data_id,
                "group": group_id,
                "namespaceId": namespace_id
            })
            api_url = f"{self.base_url}/{self.env}/diamond/api/config/getConfig?{params}"

            self._log(f"Cookie API 请求: {api_url}")
            req = urllib.request.Request(api_url, headers={
                "Cookie": cookie_header,
                "Accept": "application/json",
                "User-Agent": "Mozilla/5.0"
            })
            with urllib.request.urlopen(req, timeout=15) as resp:
                if resp.status != 200:
                    return None
                raw = resp.read().decode("utf-8")
                # 检查是否被重定向到登录页（HTML 响应）
                if raw.strip().startswith("<!") or raw.strip().startswith("<html"):
                    self._log("API 返回 HTML（可能是登录重定向），跳过")
                    return None
                body = json.loads(raw)
                content = None
                if isinstance(body, dict):
                    if body.get("data", {}).get("content"):
                        content = body["data"]["content"]
                    elif body.get("content"):
                        content = body["content"]
                if content:
                    self._log("Cookie API 获取成功")
                    return content
                return None
        except Exception as e:
            self._log(f"Cookie API 获取失败: {e}")
            return None

    async def get_config(
        self,
        data_id: str,
        group_id: str = "DEFAULT_GROUP",
        namespace_id: str = ""
    ) -> Dict[str, Any]:
        """
        获取 Diamond 配置内容

        Args:
            data_id: 配置的 dataId
            group_id: 配置的 groupId，默认为 DEFAULT_GROUP
            namespace_id: 命名空间ID，默认为空（Diamond 默认命名空间）

        Returns:
            包含配置信息的字典
        """
        result = {
            "success": False,
            "dataId": data_id,
            "groupId": group_id,
            "namespaceId": namespace_id,
            "content": None,
            "error": None
        }

        try:
            # 策略0: 优先用 saved cookies 直接 HTTP 请求（不启动浏览器，最快）
            api_content = self._try_cookie_api_fetch(data_id, group_id, namespace_id)
            if api_content:
                result["success"] = True
                result["content"] = api_content
                try:
                    json_content = json.loads(api_content)
                    print(json.dumps(json_content, indent=2, ensure_ascii=False))
                except json.JSONDecodeError:
                    print(api_content)
                return result

            # 构建 URL
            url = self._build_url(data_id, group_id, namespace_id)
            
            print("=" * 60)
            print("Diamond 配置获取工具（集成自动登录）")
            print("=" * 60)
            print(f"环境: {self.env}（预发环境）")
            print(f"DataId: {data_id}")
            print(f"GroupId: {group_id}")
            print(f"NamespaceId: {namespace_id}")
            print(f"URL: {url}")
            print("-" * 60)
            
            async with async_playwright() as p:
                # 启动浏览器
                self._log("正在启动浏览器...")
                browser = await p.chromium.launch(
                    headless=self.headless,
                    args=['--no-sandbox']
                )
                
                # 创建浏览器上下文
                context = await browser.new_context()
                
                # 创建页面
                page = await context.new_page()
                
                try:
                    # 导航到配置页面
                    print("正在访问配置页面...")
                    await page.goto(url, timeout=60000, wait_until='domcontentloaded')
                    
                    # 检查是否需要登录
                    if await self._check_login_required(page):
                        # 执行登录
                        if not await self._perform_login(page, url):
                            result["error"] = "登录失败或超时"
                            return result
                        
                        # 登录成功后，重新导航到配置页面
                        await page.goto(url, timeout=30000)
                        await asyncio.sleep(2)
                    
                    # 提取配置内容（传递参数以支持 API 获取）
                    print("\n正在提取配置内容...")
                    content = await self._extract_config_content(page, data_id, group_id, namespace_id)
                    
                    if content:
                        result["success"] = True
                        result["content"] = content
                        
                        print("-" * 60)
                        
                        # 尝试解析为 JSON
                        try:
                            json_content = json.loads(content)
                            print("配置内容（JSON格式）:")
                            print(json.dumps(json_content, indent=2, ensure_ascii=False))
                        except json.JSONDecodeError:
                            print("配置内容（文本格式）:")
                            print(content)
                        
                        print("-" * 60)
                        print("✓ 配置获取成功")
                    else:
                        result["error"] = "无法提取配置内容"
                        print(f"✗ {result['error']}")
                    
                except PlaywrightTimeout:
                    result["error"] = "页面加载超时"
                    print(f"✗ {result['error']}")
                except Exception as e:
                    result["error"] = f"访问页面时出错: {str(e)}"
                    print(f"✗ {result['error']}")
                finally:
                    await browser.close()
                    
        except Exception as e:
            result["error"] = f"未知错误: {str(e)}"
            print(f"✗ {result['error']}")
        
        print("=" * 60)
        return result


def main():
    """主函数"""
    import argparse
    
    parser = argparse.ArgumentParser(
        description="Diamond 配置获取工具（集成自动登录）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 获取配置（首次使用需要手动登录）
  %(prog)s cross.return.static.text.area ovs.base.crossReturn
  
  # 指定命名空间
  %(prog)s cross.return.static.text.area ovs.base.crossReturn ovs-base
  
  # 启用调试模式
  %(prog)s cross.return.static.text.area ovs.base.crossReturn --debug
  
  # 使用无头模式（需要已有登录凭证）
  %(prog)s cross.return.static.text.area ovs.base.crossReturn --headless
        """
    )
    
    parser.add_argument("data_id", help="配置的数据标识符")
    parser.add_argument("group_id", help="配置的分组标识符")
    parser.add_argument(
        "namespace_id",
        nargs="?",
        default="",
        help="命名空间ID（默认为空，即 Diamond 默认命名空间）"
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        help="使用无头模式（需要已有登录凭证）"
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="启用调试模式"
    )
    
    args = parser.parse_args()
    
    # 创建配置获取器
    fetcher = DiamondConfigFetcherWithLogin(
        env="pre",
        headless=args.headless,
        debug=args.debug
    )
    
    # 获取配置
    result = asyncio.run(fetcher.get_config(
        args.data_id,
        args.group_id,
        args.namespace_id
    ))
    
    # 返回状态码
    sys.exit(0 if result["success"] else 1)


if __name__ == "__main__":
    main()
