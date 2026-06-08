#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
手蓝AI内容工厂 - 全自动定时调度器
定时触发每日工作流
"""

import os
import sys
import time
import logging
import subprocess
from datetime import datetime
from pathlib import Path

# 设置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(Path(__file__).parent / '.runtime' / 'scheduler.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# 项目根目录
PROJECT_ROOT = Path(__file__).parent
sys.path.insert(0, str(PROJECT_ROOT / 'src'))

# 定时配置（从 config.yaml 读取或使用默认值）
RUN_TIMES = ["07:00", "11:30", "18:00", "22:00"]
ACCOUNT_NAME = "robot_notes"


def run_daily_workflow():
    """运行每日工作流"""
    logger.info("=" * 60)
    logger.info(f"🚀 启动每日工作流 - 账号: {ACCOUNT_NAME}")
    logger.info("=" * 60)
    
    try:
        result = subprocess.run(
            [sys.executable, str(PROJECT_ROOT / 'main.py'), '--account', ACCOUNT_NAME, '--daily-run'],
            cwd=str(PROJECT_ROOT),
            capture_output=False,
            text=True
        )
        
        if result.returncode == 0:
            logger.info("✅ 每日工作流执行成功")
        else:
            logger.error(f"❌ 每日工作流执行失败，返回码: {result.returncode}")
            
    except Exception as e:
        logger.error(f"❌ 执行工作流时出错: {e}")


def get_next_run_time(current_time):
    """获取下次运行时间"""
    current_hhmm = current_time.strftime("%H:%M")
    
    # 找到下一个运行时间
    for run_time in RUN_TIMES:
        if run_time > current_hhmm:
            return run_time
    
    # 如果今天的时间都过了，返回明天第一个
    return RUN_TIMES[0]


def wait_until(target_time):
    """等待直到目标时间"""
    now = datetime.now()
    target = datetime.strptime(f"{now.date()} {target_time}", "%Y-%m-%d %H:%M")
    
    # 如果目标时间已经过了，设置为明天
    if target < now:
        from datetime import timedelta
        target += timedelta(days=1)
    
    wait_seconds = (target - now).total_seconds()
    
    if wait_seconds > 0:
        logger.info(f"⏰ 下次运行时间: {target.strftime('%Y-%m-%d %H:%M:%S')}")
        logger.info(f"💤 等待 {wait_seconds:.0f} 秒...")
        
        # 每分钟检查一次，避免长时间等待
        while wait_seconds > 0:
            sleep_time = min(wait_seconds, 60)
            time.sleep(sleep_time)
            wait_seconds -= sleep_time


def main():
    """主调度循环"""
    logger.info("=" * 60)
    logger.info("🚀 手蓝AI内容工厂 - 全自动定时调度器")
    logger.info("=" * 60)
    logger.info(f"📋 定时配置: {', '.join(RUN_TIMES)}")
    logger.info(f"👤 账号: {ACCOUNT_NAME}")
    logger.info(f"💡 提示: 按 Ctrl+C 停止调度器")
    logger.info("")
    
    # 创建运行时目录
    (PROJECT_ROOT / '.runtime').mkdir(exist_ok=True)
    
    try:
        while True:
            now = datetime.now()
            next_time = get_next_run_time(now)
            
            wait_until(next_time)
            
            # 到达目标时间，运行工作流
            run_daily_workflow()
            
            # 避免同一分钟内多次触发
            time.sleep(60)
            
    except KeyboardInterrupt:
        logger.info("\n👋 调度器已停止")
        sys.exit(0)
    except Exception as e:
        logger.error(f"❌ 调度器异常: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
