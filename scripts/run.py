#!/usr/bin/env python3
"""NetAcad 快速启动 — 读取 config.json 配置并运行"""
import asyncio
import json
import sys
from pathlib import Path

config_path = Path(__file__).parent / "config.json"
if not config_path.exists():
    print("❌ 未找到 config.json")
    sys.exit(1)

config = json.loads(config_path.read_text())
if not config.get("password"):
    print("❌ 请在 config.json 中填写密码")
    sys.exit(1)

from netacad_auto import NetAcadLearner

async def main():
    learner = NetAcadLearner(
        email=config["email"],
        password=config["password"],
        course_name=config.get("course_name", "网络信息安全技术"),
        headless=config.get("headless", False),
        course_url=config.get("course_url") or None,
    )
    await learner.start()

if __name__ == "__main__":
    asyncio.run(main())
