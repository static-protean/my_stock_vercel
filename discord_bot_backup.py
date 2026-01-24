#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
==================================
A股自选股智能分析系统 - Discord机器人
==================================

用于在Discord中提供股票分析服务的机器人
支持Slash命令，提供实时股票分析和大盘复盘
"""

# 导入标准库
import os
import sys
import logging
import asyncio
import argparse
import uuid
from datetime import datetime
from typing import Optional, Any, Callable

# 导入第三方库
try:
    import discord
    from discord.ext import commands
    from discord import app_commands
except ImportError:
    print("请先安装discord.py依赖：pip install discord.py>=2.0.0")
    sys.exit(1)

# 添加项目根目录到Python路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# 导入项目模块
from config import get_config, Config
from main import run_full_analysis, run_market_review
from notification import NotificationService

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)-8s | %(message)s',
    datefmt='%H:%M:%S'
)
logger = logging.getLogger(__name__)

# 获取配置
config = get_config()

# 常量定义
DEFAULT_TIMEOUT = 300  # 默认超时时间，单位：秒
STOCK_ANALYSIS_TIMEOUT = 600  # 股票分析超时时间，单位：秒
MARKET_REVIEW_TIMEOUT = 300  # 大盘复盘超时时间，单位：秒


class CommandHandler:
    """命令处理器基类，封装通用的命令处理逻辑"""
    
    def __init__(self, bot: commands.Bot):
        """初始化命令处理器
        
        Args:
            bot: Discord机器人实例
        """
        self.bot = bot
        self.logger = logging.getLogger(f"{__name__}.CommandHandler")
        
    async def execute_with_error_handling(
        self,
        interaction: discord.Interaction,
        command_name: str,
        action: Callable,
        *args,
        **kwargs
    ):
        """执行命令并处理错误
        
        Args:
            interaction: Discord交互对象
            command_name: 命令名称
            action: 要执行的命令函数
            *args: 命令函数参数
            **kwargs: 命令函数关键字参数
        """
        # 生成请求ID，用于跟踪请求
        request_id = str(uuid.uuid4())[:8]
        user_info = f"{interaction.user} (ID: {interaction.user.id})"
        guild_info = f"服务器: {interaction.guild.name} (ID: {interaction.guild.id})" if interaction.guild else "私人消息"
        
        self.logger.info(f"请求ID: {request_id} | 用户: {user_info} | 来源: {guild_info} | 请求命令: {command_name}")
        
        try:
            # 执行命令
            result = await action(*args, **kwargs)
            self.logger.info(f"请求ID: {request_id} | 命令 {command_name} 执行成功")
            return result
        except ValueError as e:
            error_msg = f"❌ 输入错误：{str(e)}"
            await interaction.followup.send(error_msg, ephemeral=False)
            self.logger.error(f"请求ID: {request_id} | 命令 {command_name} 输入错误：{e}")
        except asyncio.TimeoutError:
            error_msg = f"❌ 命令执行超时，请稍后重试。"
            await interaction.followup.send(error_msg, ephemeral=False)
            self.logger.error(f"请求ID: {request_id} | 命令 {command_name} 执行超时")
        except Exception as e:
            # 根据错误类型提供不同的用户提示
            error_type = type(e).__name__
            if "APIError" in error_type or "NetworkError" in error_type:
                error_msg = f"❌ 网络或API错误，请稍后重试。"
            elif "PermissionError" in error_type:
                error_msg = f"❌ 权限不足，无法执行该命令。"
            else:
                error_msg = f"❌ 执行命令过程中发生错误，请稍后重试或联系管理员。"
            
            await interaction.followup.send(error_msg, ephemeral=False)
            self.logger.error(f"请求ID: {request_id} | 命令 {command_name} 执行异常", exc_info=True)
        
        return None


class AsyncTaskManager:
    """异步任务管理器，统一处理异步任务"""
    
    def __init__(self, bot: commands.Bot):
        """初始化异步任务管理器
        
        Args:
            bot: Discord机器人实例
        """
        self.bot = bot
        self.logger = logging.getLogger(f"{__name__}.AsyncTaskManager")
        self.default_timeout = DEFAULT_TIMEOUT
    
    async def run_task_with_timeout(
        self,
        task_name: str,
        task_func: Callable,
        *args,
        timeout: int = None,
        **kwargs
    ):
        """运行带超时的异步任务
        
        Args:
            task_name: 任务名称
            task_func: 要执行的任务函数
            *args: 任务函数参数
            timeout: 超时时间，单位：秒
            **kwargs: 任务函数关键字参数
        
        Returns:
            任务执行结果
        """
        timeout = timeout or self.default_timeout
        self.logger.info(f"开始执行任务：{task_name}，超时时间：{timeout}秒")
        
        try:
            # 创建任务
            task = asyncio.create_task(task_func(*args, **kwargs))
            
            # 运行任务并设置超时
            result = await asyncio.wait_for(task, timeout=timeout)
            self.logger.info(f"任务 {task_name} 执行成功")
            return result
        except asyncio.TimeoutError:
            self.logger.error(f"任务 {task_name} 执行超时（{timeout}秒）")
            raise Exception(f"任务执行超时，请稍后重试")
        except Exception as e:
            self.logger.error(f"任务 {task_name} 执行异常：{e}", exc_info=True)
            raise
    
    async def run_sync_task(
        self,
        task_name: str,
        sync_func: Callable,
        *args,
        timeout: int = None,
        **kwargs
    ):
        """运行同步任务（在单独线程中执行）
        
        Args:
            task_name: 任务名称
            sync_func: 要执行的同步函数
            *args: 函数参数
            timeout: 超时时间，单位：秒
            **kwargs: 函数关键字参数
        
        Returns:
            任务执行结果
        """
        async def wrapper():
            """同步函数的异步包装器"""
            return await asyncio.to_thread(sync_func, *args, **kwargs)
        
        return await self.run_task_with_timeout(
            task_name,
            wrapper,
            timeout=timeout
        )


class StockAnalysisBot(commands.Bot):
    """股票分析Discord机器人"""
    
    def __init__(self):
        """初始化机器人"""
        intents = discord.Intents.default()
        intents.message_content = True
        
        super().__init__(
            command_prefix='!',  # 传统命令前缀（主要用于调试）
            intents=intents,
            description='A股自选股智能分析机器人'
        )
        
        # 初始化服务实例（复用，避免重复创建）
        self.notification_service = NotificationService()
        
        # 初始化命令处理器
        self.command_handler = CommandHandler(self)
        
        # 初始化异步任务管理器
        self.task_manager = AsyncTaskManager(self)
        
        self.logger = logging.getLogger(f"{__name__}.StockAnalysisBot")
        self.logger.info("机器人初始化完成")
    
    async def setup_hook(self):
        """设置钩子，用于加载命令"""
        # 同步全局命令
        await self.tree.sync()
        self.logger.info("Slash命令已同步")
    
    async def on_ready(self):
        """机器人上线事件"""
        self.logger.info(f"机器人已上线：{self.user.name} ({self.user.id})")
        self.logger.info(f"已连接到 {len(self.guilds)} 个服务器")
        
        # 设置机器人状态，从配置中读取
        await self.change_presence(
            status=discord.Status.online,
            activity=discord.Game(name=config.discord_bot_status)
        )


# 创建机器人实例
bot = StockAnalysisBot()


def _run_stock_analysis(stock_code: str, full_report: bool = False):
    """运行股票分析的包装函数
    
    用于在单独线程中执行，避免阻塞事件循环
    
    Args:
        stock_code: 股票代码
        full_report: 是否生成完整报告
    
    Returns:
        分析结果
    """
    # 创建临时的命令行参数对象
    args = argparse.Namespace(
        debug=True,
        dry_run=False,
        no_notify=False,
        single_notify=False,
        workers=None,
        schedule=False,
        market_review=False,
        no_market_review=not full_report,
        webui=False,
        webui_only=False,
        stocks=None
    )
    
    # 创建独立配置副本，避免修改全局配置
    bot_config = Config()
    
    # 运行分析
    return run_full_analysis(
        config=bot_config,
        args=args,
        stock_codes=[stock_code]
    )


async def _execute_stock_analysis(stock_code: str, full_report: bool = False):
    """执行股票分析的异步包装函数
    
    Args:
        stock_code: 股票代码
        full_report: 是否生成完整报告
    
    Returns:
        分析结果
    """
    # 验证股票代码格式
    import re
    if not re.match(r'^\d{6}$', stock_code):
        raise ValueError(f"无效的股票代码格式：{stock_code}，请输入6位数字股票代码")
    
    return await bot.task_manager.run_sync_task(
        f"stock_analysis_{stock_code}",
        _run_stock_analysis,
        stock_code=stock_code,
        full_report=full_report,
        timeout=STOCK_ANALYSIS_TIMEOUT
    )


async def _execute_market_review():
    """执行大盘复盘的异步包装函数
    
    Returns:
        复盘结果
    """
    return await bot.task_manager.run_sync_task(
        "market_review",
        run_market_review,
        notifier=bot.notification_service,
        analyzer=None,
        search_service=None,
        timeout=MARKET_REVIEW_TIMEOUT
    )


@bot.tree.command(
    name="stock_analyze",
    description="分析指定股票代码"
)
async def stock_analyze(
    interaction: discord.Interaction,
    stock_code: str,
    full_report: bool = False
):
    """分析指定股票代码
    
    Args:
        interaction: Discord交互对象
        stock_code: 股票代码
        full_report: 是否生成完整报告
    """
    await interaction.response.defer(ephemeral=False)
    
    # 格式化股票代码
    stock_code = stock_code.strip()
    
    async def action():
        """股票分析操作"""
        # 发送分析开始消息
        await interaction.followup.send(
            f"🔄 正在分析股票：{stock_code}...",
            ephemeral=False
        )
        
        result = await _execute_stock_analysis(stock_code, full_report)
        
        # 发送成功消息
        await interaction.followup.send(
            f"✅ 股票分析完成！{stock_code} 的分析报告已生成。",
            ephemeral=False
        )
        bot.logger.info(f"股票分析完成：{stock_code}")
        return result
    
    # 使用命令处理器执行命令
    await bot.command_handler.execute_with_error_handling(
        interaction,
        "stock_analyze",
        action
    )


@bot.tree.command(
    name="market_review",
    description="获取大盘复盘"
)
async def market_review_command(
    interaction: discord.Interaction
):
    """获取大盘复盘
    
    Args:
        interaction: Discord交互对象
    """
    await interaction.response.defer(ephemeral=False)
    
    async def action():
        """大盘复盘操作"""
        # 发送复盘开始消息
        await interaction.followup.send(
            "🔄 正在生成大盘复盘报告...",
            ephemeral=False
        )
        
        review_result = await _execute_market_review()
        
        if review_result:
            await interaction.followup.send(
                "✅ 大盘复盘完成！报告已生成。",
                ephemeral=False
            )
            bot.logger.info("大盘复盘完成")
        else:
            await interaction.followup.send(
                "❌ 大盘复盘失败，请确保相关服务已配置。",
                ephemeral=False
            )
            bot.logger.error("大盘复盘失败")
        
        return review_result
    
    # 使用命令处理器执行命令
    await bot.command_handler.execute_with_error_handling(
        interaction,
        "market_review",
        action
    )


@bot.tree.command(
    name="help",
    description="查看帮助信息"
)
async def help_command(
    interaction: discord.Interaction
):
    """查看帮助信息
    
    Args:
        interaction: Discord交互对象
    """
    help_message = f"""
📊 **A股智能分析机器人帮助**

### 支持的命令：

1. `/stock_analyze <stock_code> [full_report]`
   - 分析指定股票代码
   - `stock_code`: 股票代码，如 600519
   - `full_report`: 可选，是否生成完整报告（包含大盘）

2. `/market_review`
   - 获取大盘复盘报告

3. `/help`
   - 查看此帮助信息

### 示例：
- `/stock_analyze 600519` - 分析贵州茅台
- `/stock_analyze 300750 true` - 生成宁德时代的完整报告
- `/market_review` - 获取大盘复盘

### 配置说明：
机器人使用项目的.env配置文件，需要确保配置正确的API密钥和通知渠道。

📈 数据来源：Tushare、Efinance
🤖 AI分析：Gemini
"""
    
    await interaction.response.send_message(
        help_message,
        ephemeral=False,
        embed=None
    )
    bot.logger.info(f"用户 {interaction.user} 请求帮助信息")


@bot.tree.command(
    name="about",
    description="关于机器人"
)
async def about_command(
    interaction: discord.Interaction
):
    """关于机器人
    
    Args:
        interaction: Discord交互对象
    """
    about_message = f"""
🤖 **关于A股智能分析机器人**

### 项目信息：
- **名称**：A股自选股智能分析系统
- **版本**：v1.0.0
- **作者**：daily_stock_analysis团队
- **GitHub**：https://github.com/ZhuLinsen/daily_stock_analysis

### 功能特点：
- ✅ 多数据源支持（Tushare、Efinance）
- ✅ AI驱动的智能分析（Gemini）
- ✅ 实时新闻整合
- ✅ 多渠道通知推送
- ✅ Discord机器人支持
- ✅ 大盘复盘分析
- ✅ 技术指标计算

### 联系方式：
如有问题或建议，欢迎在GitHub上提交Issue或PR。
"""
    
    await interaction.response.send_message(
        about_message,
        ephemeral=False,
        embed=None
    )
    bot.logger.info(f"用户 {interaction.user} 请求关于信息")


def main():
    """主函数"""
    # 检查必要配置
    if not config.discord_bot_token:
        logger.error("请在.env文件中配置DISCORD_BOT_TOKEN")
        return 1
    
    logger.info("正在启动Discord机器人...")
    
    try:
        # 启动机器人
        bot.run(config.discord_bot_token)
        return 0
    except KeyboardInterrupt:
        logger.info("机器人已手动停止")
        return 0
    except Exception as e:
        logger.error(f"机器人启动失败：{e}", exc_info=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())