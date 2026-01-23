#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
==================================
A股自选股智能分析系统 - Discord机器人
==================================

用于在Discord中提供股票分析服务的机器人
支持Slash命令，提供实时股票分析和大盘复盘
"""

import os
import sys
import logging
import asyncio
from datetime import datetime

# 添加项目根目录到Python路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)-8s | %(message)s',
    datefmt='%H:%M:%S'
)
logger = logging.getLogger(__name__)

# 导入Discord相关模块
try:
    import discord
    from discord.ext import commands
    from discord import app_commands
except ImportError:
    logger.error("请先安装discord.py依赖：pip install discord.py>=2.0.0")
    sys.exit(1)

# 导入项目模块
from config import get_config
from main import run_full_analysis, run_market_review
from notification import NotificationService

# 获取配置
config = get_config()

class StockAnalysisBot(commands.Bot):
    """股票分析Discord机器人"""
    
    def __init__(self):
        """初始化机器人"""
        intents = discord.Intents.default()
        intents.message_content = True
        
        super().__init__(
            intents=intents,
            description='A股自选股智能分析机器人'
        )
        
        # 初始化服务实例（复用，避免重复创建）
        self.notification_service = NotificationService()
        
        logger.info("机器人初始化完成")
    
    async def setup_hook(self):
        """设置钩子，用于加载命令"""
        # 同步全局命令
        await self.tree.sync()
        logger.info("Slash命令已同步")
    
    async def on_ready(self):
        """机器人上线事件"""
        logger.info(f"机器人已上线：{self.user.name} ({self.user.id})")
        logger.info(f"已连接到 {len(self.guilds)} 个服务器")
        
        # 设置机器人状态
        await self.change_presence(
            status=discord.Status.online,
            activity=discord.Game(name="A股智能分析 | /help")
        )

# 创建机器人实例
bot = StockAnalysisBot()

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
    
    logger.info(f"用户 {interaction.user} 请求分析股票：{stock_code}")
    
    try:
        # 直接调用run_full_analysis，传递关键参数
        # 避免使用argparse.Namespace，减少耦合
        result = await asyncio.to_thread(
            _run_stock_analysis,
            stock_code=stock_code,
            full_report=full_report
        )
        
        # 发送成功消息
        await interaction.followup.send(
            f"✅ 股票分析完成！{stock_code} 的分析报告已生成。",
            ephemeral=False
        )
        logger.info(f"股票分析完成：{stock_code}")
            
    except ValueError as e:
        await interaction.followup.send(
            f"❌ 股票代码错误：{str(e)}",
            ephemeral=False
        )
        logger.error(f"股票代码错误：{stock_code} - {e}")
    except Exception as e:
        await interaction.followup.send(
            f"❌ 分析过程中发生错误，请稍后重试或联系管理员。",
            ephemeral=False
        )
        logger.error(f"股票分析异常：{stock_code} - {e}", exc_info=True)

@bot.tree.command(
    name="market_review",
    description="获取大盘复盘"
)
async def market_review(
    interaction: discord.Interaction
):
    """获取大盘复盘
    
    Args:
        interaction: Discord交互对象
    """
    await interaction.response.defer(ephemeral=False)
    
    logger.info(f"用户 {interaction.user} 请求大盘复盘")
    
    try:
        # 运行大盘复盘（在单独线程中执行，避免阻塞事件循环）
        review_result = await asyncio.to_thread(
            run_market_review,
            notifier=bot.notification_service,
            analyzer=None,
            search_service=None
        )
        
        if review_result:
            await interaction.followup.send(
                "✅ 大盘复盘完成！报告已生成。",
                ephemeral=False
            )
            logger.info("大盘复盘完成")
        else:
            await interaction.followup.send(
                "❌ 大盘复盘失败，请确保相关服务已配置。",
                ephemeral=False
            )
            logger.error("大盘复盘失败")
            
    except Exception as e:
        await interaction.followup.send(
            f"❌ 大盘复盘过程中发生错误，请稍后重试或联系管理员。",
            ephemeral=False
        )
        logger.error(f"大盘复盘异常：{e}", exc_info=True)

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

def _run_stock_analysis(stock_code: str, full_report: bool = False):
    """运行股票分析的包装函数
    
    用于在单独线程中执行，避免阻塞事件循环
    
    Args:
        stock_code: 股票代码
        full_report: 是否生成完整报告
    
    Returns:
        分析结果
    """
    import argparse
    from config import Config
    from main import run_full_analysis
    
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
