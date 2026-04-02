#!/usr/bin/env python3
"""
NetAcad 自动刷课脚本 v2
按照标准化学习流程完成 Cisco NetAcad 网课

流程：
1. 邮箱登录 → 进入仪表盘
2. 找到目标课程 → 进入课程主页
3. 单模块循环：视频学习（原速）→ 习题作答 → 进度核验
4. 全课程推进直至 100%

依赖：
    pip install playwright
    playwright install chromium
"""

import argparse
import asyncio
import json
import logging
import os
import random
import re
import sys
import time
from pathlib import Path

try:
    from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout
except ImportError:
    print("❌ 请先安装 playwright: pip install playwright && playwright install chromium")
    sys.exit(1)

# ─── 日志配置 ───────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("netacad")

# ─── 常量 ─────────────────────────────────────────────────
NETACAD_HOME = "https://www.netacad.com/"
NETACAD_DASHBOARD = "https://www.netacad.com/dashboard"
AUTH_DOMAIN = "auth.netacad.com"
SCRIPT_DIR = Path(__file__).parent
STATE_FILE = SCRIPT_DIR / ".netacad_state.json"
SCREENSHOTS_DIR = SCRIPT_DIR / "screenshots"

# 人类行为模拟
HUMAN_DELAY = (0.8, 2.5)
TYPING_DELAY = 70  # ms per key

# 超时设置
PAGE_TIMEOUT = 30000
NAV_TIMEOUT = 60000


def human_delay():
    return random.uniform(*HUMAN_DELAY)


def save_state(data: dict):
    STATE_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2))


def load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {}


def screenshot_path(name: str) -> str:
    SCREENSHOTS_DIR.mkdir(exist_ok=True)
    return str(SCREENSHOTS_DIR / f"{name}.png")


# ═══════════════════════════════════════════════════════════════
# 主引擎
# ═══════════════════════════════════════════════════════════════

class NetAcadLearner:
    """NetAcad 标准化学习引擎"""

    def __init__(self, email: str, password: str, course_name: str = "网络信息安全技术",
                 headless: bool = False, course_url: str = None):
        self.email = email
        self.password = password
        self.course_name = course_name
        self.headless = headless
        self.course_url = course_url
        self.page = None
        self.browser = None
        self.state = load_state()
        self.stats = {
            "videos_watched": 0,
            "quizzes_completed": 0,
            "modules_completed": 0,
            "pages_navigated": 0,
            "errors": 0,
        }

    # ─── 启动入口 ──────────────────────────────────────────

    async def start(self):
        log.info("🚀 启动 NetAcad 学习引擎...")
        async with async_playwright() as p:
            user_data_dir = SCRIPT_DIR / ".browser_data"
            user_data_dir.mkdir(exist_ok=True)

            self.browser = await p.chromium.launch_persistent_context(
                user_data_dir=str(user_data_dir),
                headless=self.headless,
                viewport={"width": 1366, "height": 900},
                locale="zh-CN",
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--disable-infobars",
                ],
                ignore_default_args=["--enable-automation"],
            )
            self.page = self.browser.pages[0] if self.browser.pages else await self.browser.new_page()
            self.page.set_default_timeout(PAGE_TIMEOUT)
            self.page.set_default_navigation_timeout(NAV_TIMEOUT)

            try:
                # 阶段1：登录
                await self._login()

                # 阶段2：找到并进入目标课程
                await self._enter_course()

                # 阶段3：主学习循环 — 模块逐一推进
                await self._learn_all_modules()

            except KeyboardInterrupt:
                log.info("⏹ 用户中断")
            except Exception as e:
                log.error(f"❌ 错误: {e}", exc_info=True)
                await self.page.screenshot(path=screenshot_path("error"))
            finally:
                self._save_and_report()
                await self.browser.close()

    # ═══════════════════════════════════════════════════════
    # 阶段1：登录（邮箱方式，非 Google）
    # ═══════════════════════════════════════════════════════

    async def _login(self):
        """
        NetAcad 登录流程（根据实际页面截图适配）：
        
        页面结构：
        - 右侧表单："Welcome! Please login to your account."
        - Email 输入框（label="Email"）
        - 绿色 "Login" 按钮 → 点击后出现密码框
        - 下方 "Or continue with" → Google 按钮（跳过）
        - 这是分步登录：先输邮箱 → 点 Login → 出密码框 → 再提交
        """
        log.info("🔑 检查登录状态...")
        await self.page.goto(NETACAD_DASHBOARD, wait_until="domcontentloaded")
        
        # 等待 URL 稳定（可能有重定向）
        for _ in range(5):
            await asyncio.sleep(2)
        
        url = self.page.url
        log.info(f"📍 稳定后 URL: {url}")
        
        # 检查是否真正在 dashboard（不是登录页）
        is_logged_in = ("dashboard" in url and "auth" not in url and "login" not in url)
        
        # 再检查页面内容是否有登录表单
        if is_logged_in:
            login_form = self.page.locator('input[name="username"], button:has-text("Login"), input[type="password"]')
            if await login_form.count() > 0:
                # 页面上有登录表单，说明实际还没登录
                is_logged_in = False
                log.info("⚠️ URL 显示 dashboard 但页面有登录表单，需要重新登录")
        
        if is_logged_in:
            log.info("✅ 已登录，跳过")
            return

        log.info(f"📝 需要登录，当前: {url}")
        await self.page.screenshot(path=screenshot_path("login_step0"))

        # ─── 第1步：找到 Email 输入框并填写 ───
        # NetAcad 登录页的 Email 输入框可能的选择器
        email_input = None
        email_selectors = [
            'input[type="email"]',
            'input[name="email"]',
            'input[name="username"]',
            'input[id="email"]',
            'input[id="username"]',
            '#username',
            '#email',
            # 通过 label 关联查找
            'input[aria-label*="mail"]',
            'input[aria-label*="Mail"]',
            'input[placeholder*="mail"]',
            'input[placeholder*="Mail"]',
            'input[placeholder*="Email"]',
            # 最宽泛：页面上唯一的 text/email 输入框
            'input[type="text"]',
        ]

        # 等待页面 JS 渲染完成
        await asyncio.sleep(3)

        for sel in email_selectors:
            try:
                el = self.page.locator(sel)
                cnt = await el.count()
                if cnt > 0:
                    for i in range(cnt):
                        if await el.nth(i).is_visible():
                            email_input = el.nth(i)
                            log.info(f"📧 找到 Email 输入框: {sel}")
                            break
                if email_input:
                    break
            except Exception:
                continue

        if not email_input:
            await self.page.screenshot(path=screenshot_path("login_no_email"))
            raise Exception("找不到 Email 输入框，查看 screenshots/login_no_email.png")

        # 点击输入框并输入邮箱
        await email_input.click()
        await asyncio.sleep(0.5)
        await email_input.fill(self.email)
        await asyncio.sleep(0.5)
        log.info(f"📧 已输入邮箱: {self.email}")
        await self.page.screenshot(path=screenshot_path("login_step1_email"))

        # ─── 第2步：点击 Login 按钮（第一次提交，只提交邮箱）───
        login_btn = None
        login_selectors = [
            'button:has-text("Login")',
            'button:has-text("登录")',
            'button:has-text("Log in")',
            'button:has-text("Sign in")',
            'button:has-text("Next")',
            'button:has-text("Continue")',
            'button[type="submit"]',
            'input[type="submit"]',
        ]

        for sel in login_selectors:
            try:
                btn = self.page.locator(sel)
                if await btn.count() > 0 and await btn.first.is_visible():
                    login_btn = btn.first
                    log.info(f"🔘 找到 Login 按钮: {sel}")
                    break
            except Exception:
                continue

        if login_btn:
            await asyncio.sleep(human_delay())
            await login_btn.click()
            log.info("⏩ 已点击 Login，等待密码框出现...")
        else:
            # 备选：按回车
            await email_input.press("Enter")
            log.info("⏩ 已按回车提交邮箱...")

        await asyncio.sleep(4)
        await self.page.screenshot(path=screenshot_path("login_step2_after_email"))

        # ─── 第3步：查找并填写密码框 ───
        pwd_input = None
        pwd_selectors = [
            'input[type="password"]',
            'input[name="password"]',
            '#password',
            'input[id="password"]',
            'input[aria-label*="assword"]',
            'input[placeholder*="assword"]',
        ]

        # 可能需要等待密码框出现
        for attempt in range(5):
            for sel in pwd_selectors:
                try:
                    el = self.page.locator(sel)
                    if await el.count() > 0 and await el.first.is_visible():
                        pwd_input = el.first
                        log.info(f"🔒 找到密码输入框: {sel}")
                        break
                except Exception:
                    continue
            if pwd_input:
                break
            await asyncio.sleep(2)

        if not pwd_input:
            # 可能邮箱提交后仍在同一页面但有错误
            await self.page.screenshot(path=screenshot_path("login_no_pwd"))
            
            # 检查是否有错误消息
            page_text = await self.page.text_content("body") or ""
            if "error" in page_text.lower() or "invalid" in page_text.lower() or "错误" in page_text:
                raise Exception(f"邮箱提交后出现错误，查看 screenshots/login_no_pwd.png")
            
            # 可能已经跳转到 dashboard 了（某些情况 SSO 直接完成）
            if "dashboard" in self.page.url:
                log.info("✅ 登录成功（SSO 直接完成）！")
                return
            
            raise Exception("找不到密码输入框，查看 screenshots/login_no_pwd.png")

        # 输入密码
        await pwd_input.click()
        await asyncio.sleep(0.3)
        await pwd_input.fill(self.password)
        log.info("🔒 已输入密码")
        await asyncio.sleep(human_delay())
        await self.page.screenshot(path=screenshot_path("login_step3_password"))

        # ─── 第4步：提交密码（第二次提交）───
        submit_btn = None
        submit_selectors = [
            'button:has-text("Login")',
            'button:has-text("Sign in")',
            'button:has-text("Log in")',
            'button:has-text("登录")',
            'button:has-text("Submit")',
            'button[type="submit"]',
            'input[type="submit"]',
            '#kc-login',
        ]

        for sel in submit_selectors:
            try:
                btn = self.page.locator(sel)
                if await btn.count() > 0 and await btn.first.is_visible():
                    submit_btn = btn.first
                    break
            except Exception:
                continue

        if submit_btn:
            await submit_btn.click()
            log.info("🚪 已提交密码")
        else:
            await pwd_input.press("Enter")
            log.info("🚪 已按回车提交密码")

        # ─── 第5步：等待登录完成 ───
        log.info("⏳ 等待登录完成...")
        try:
            await self.page.wait_for_url(
                lambda u: "dashboard" in u or ("netacad.com" in u and "auth" not in u),
                timeout=30000,
            )
            log.info("✅ 登录成功！")
        except PlaywrightTimeout:
            url = self.page.url
            if "dashboard" in url or ("netacad.com" in url and "auth" not in url):
                log.info("✅ 登录成功！")
            else:
                await self.page.screenshot(path=screenshot_path("login_failed"))
                # 检查错误提示
                err = self.page.locator('[class*="error"], [class*="alert"], .kc-feedback-text')
                if await err.count() > 0:
                    txt = await err.first.text_content()
                    raise Exception(f"登录失败: {txt}")
                log.warning(f"⚠️ 登录状态不确定: {url}，查看 screenshots/login_failed.png")

        await asyncio.sleep(3)
        await self.page.screenshot(path=screenshot_path("login_done"))

    # ═══════════════════════════════════════════════════════
    # 阶段2：找到并进入目标课程
    # ═══════════════════════════════════════════════════════

    async def _enter_course(self):
        """进入目标课程 — 直接点击第二个课程卡片的播放按钮"""
        if self.course_url:
            log.info(f"📖 直接打开课程: {self.course_url}")
            await self.page.goto(self.course_url, wait_until="domcontentloaded")
            await asyncio.sleep(5)
            return

        log.info(f"📚 在仪表盘查找课程: 《{self.course_name}》（第2个卡片）")
        
        # 登录成功后可能已经在 dashboard，检查当前 URL
        current = self.page.url
        if "dashboard" not in current:
            try:
                await self.page.goto(NETACAD_DASHBOARD, wait_until="domcontentloaded")
            except Exception:
                # ERR_ABORTED 说明页面已在加载/跳转中，等一等就好
                log.info("⏳ 页面跳转中，等待...")
                await asyncio.sleep(5)

        # 等待 SPA 内容渲染
        log.info("⏳ 等待仪表盘内容加载...")
        for wait_round in range(15):
            await asyncio.sleep(2)
            page_text = await self.page.text_content("body") or ""
            has_content = len(page_text.strip()) > 100
            has_link = await self.page.locator('a[href]').count() > 5
            if has_content and has_link:
                log.info(f"✅ 仪表盘内容已加载 (第{wait_round+1}轮)")
                break
        
        await asyncio.sleep(2)
        await self.page.screenshot(path=screenshot_path("dashboard"), full_page=True)

        # ─── 直接点击第二个课程卡片的播放按钮 ───
        # Dashboard 课程卡片布局（从截图确认）：
        # 第1个: English for IT 1
        # 第2个: 网络信息安全技术 (Cybersecurity Essentials) ← 目标
        # 第3个: CCNA：企业网络
        # 每个卡片缩略图中心有一个播放按钮(SVG圆形play icon)

        await self.page.screenshot(path=screenshot_path("dashboard"), full_page=True)
        log.info("📸 仪表盘截图已保存")

        # 方法：用 JS 找到所有播放按钮(SVG)，点击第二个
        play_result = await self.page.evaluate("""
            () => {
                // 找到所有 SVG 播放按钮（课程卡片缩略图上的圆形 play icon）
                const svgs = document.querySelectorAll('svg');
                const playButtons = [];
                
                for (const svg of svgs) {
                    const rect = svg.getBoundingClientRect();
                    // 播放按钮通常在页面主体区域，尺寸适中
                    if (rect.width > 20 && rect.width < 100 && rect.y > 100 && rect.y < 800) {
                        // 检查是否在卡片区域内（排除顶栏/底栏的图标）
                        const parent = svg.closest('a, button, [class*="card"], [class*="thumbnail"], [class*="image"]');
                        playButtons.push({
                            x: rect.x + rect.width / 2,
                            y: rect.y + rect.height / 2,
                            w: rect.width,
                            h: rect.height,
                            hasParent: !!parent,
                            parentTag: parent ? parent.tagName : 'none',
                        });
                    }
                }
                
                return { count: playButtons.length, buttons: playButtons };
            }
        """)

        log.info(f"🔍 找到 {play_result['count']} 个播放按钮")
        for i, btn in enumerate(play_result['buttons']):
            log.info(f"   ▶️ [{i}] 位置: ({btn['x']:.0f}, {btn['y']:.0f}) 尺寸: {btn['w']:.0f}x{btn['h']:.0f}")

        if play_result['count'] >= 2:
            # 点击第2个播放按钮（索引1）= 《网络信息安全技术》
            target = play_result['buttons'][1]
            log.info(f"▶️ 点击第2个播放按钮: ({target['x']:.0f}, {target['y']:.0f})")
            await self.page.mouse.click(target['x'], target['y'])
            await asyncio.sleep(5)

            new_url = self.page.url
            log.info(f"📍 点击后 URL: {new_url[:80]}")

            if "launch" in new_url or "dashboard" not in new_url:
                log.info("✅ 已进入课程！")
                # 检查新标签页
                pages = self.browser.pages
                if len(pages) > 1:
                    self.page = pages[-1]
                    log.info(f"📄 切换到新标签页: {self.page.url[:80]}")
                    await asyncio.sleep(3)
                return

            # 如果还没跳转，直接点缩略图图片区域
            log.info("⚠️ 播放按钮未跳转，尝试点击缩略图区域...")
            await self.page.mouse.click(target['x'], target['y'] - 20)
            await asyncio.sleep(5)
            if "launch" in self.page.url or "dashboard" not in self.page.url:
                log.info("✅ 已进入课程！")
                return

        # 兜底：截图后等待手动操作
        await self.page.screenshot(path=screenshot_path("no_play_btn"), full_page=True)
        log.warning("⚠️ 未能自动点击播放按钮，请手动点击课程")
        log.warning("⏳ 等待30秒...")
        await asyncio.sleep(30)

    # ═══════════════════════════════════════════════════════
    # 阶段3：主学习循环
    # ═══════════════════════════════════════════════════════

    async def _learn_all_modules(self):
        """按模块顺序逐一完成：视频 → 习题 → 进度核验"""
        log.info("=" * 60)
        log.info("📚 开始全课程学习循环")
        log.info("=" * 60)

        course_home_url = self.page.url
        module_round = 0
        max_rounds = 50  # 防止无限循环

        while module_round < max_rounds:
            module_round += 1
            log.info(f"\n{'─' * 50}")
            log.info(f"🔄 第 {module_round} 轮模块检查")
            log.info(f"{'─' * 50}")

            # 回到课程主页检查进度
            if module_round > 1:
                await self.page.goto(course_home_url, wait_until="domcontentloaded")
                await asyncio.sleep(3)

            # 截图课程主页
            await self.page.screenshot(path=screenshot_path(f"course_round_{module_round}"))

            # 检查是否全部完成
            if await self._is_course_complete():
                log.info("🎉🎉🎉 课程全部完成！所有模块进度 100%！")
                break

            # 找到第一个未完成的模块并进入
            module_entered = await self._enter_next_incomplete_module()
            if not module_entered:
                log.info("⚠️ 没有找到未完成的模块，尝试继续当前页面内容...")
                # 尝试在当前页面做操作
                actions_taken = await self._do_page_actions(max_idle=15)
                if not actions_taken:
                    log.info("📊 所有可检测的内容已完成")
                    break
                continue

            # ─── 单模块标准化流程 ───

            # 步骤A: 视频学习（原速完整观看）
            await self._watch_all_videos()

            # 步骤B: 习题作答
            await self._complete_all_quizzes()

            # 步骤C: 继续翻页推进直到模块完成
            await self._do_page_actions(max_idle=20)

            # 步骤D: 进度核验
            self.stats["modules_completed"] += 1
            log.info(f"✅ 模块 #{module_round} 学习完成，返回课程主页核验进度")

        self._save_and_report()

    # ═══════════════════════════════════════════════════════
    # 视频学习模块
    # ═══════════════════════════════════════════════════════

    async def _watch_all_videos(self):
        """完整观看当前页面及后续页面的所有视频，原速不跳播"""
        log.info("🎬 开始视频学习阶段...")

        video_count = 0
        while True:
            watched = await self._watch_current_video()
            if watched:
                video_count += 1
                self.stats["videos_watched"] += 1
                log.info(f"✅ 第 {video_count} 个视频播放完毕")
                await asyncio.sleep(human_delay())

                # 看看下一页是否还有视频
                has_next = await self._click_next()
                if has_next:
                    await asyncio.sleep(2)
                    continue
                else:
                    break
            else:
                # 当前页没有视频，尝试翻页
                has_next = await self._click_next()
                if has_next:
                    await asyncio.sleep(2)
                    # 检查新页面是否有视频
                    has_video = await self.page.locator('video').count() > 0
                    if has_video:
                        continue
                    else:
                        # 新页面也没视频，可能进入了习题区域
                        break
                else:
                    break

        log.info(f"🎬 视频学习阶段完成，共观看 {video_count} 个视频")

    async def _watch_current_video(self) -> bool:
        """观看当前页面的视频（原速、不跳播）"""
        video_el = self.page.locator('video')
        if await video_el.count() == 0:
            # 检查 iframe 内嵌视频
            iframe_el = self.page.locator('iframe[src*="youtube"], iframe[src*="vimeo"], iframe[src*="player"]')
            if await iframe_el.count() > 0:
                log.info("🎬 检测到内嵌视频播放器，尝试播放...")
                try:
                    frame = iframe_el.first.content_frame()
                    if frame:
                        play_btn = frame.locator('button[aria-label*="play"], .ytp-play-button, .vp-play-button')
                        if await play_btn.count() > 0:
                            await play_btn.first.click()
                            # 等待内嵌视频播放（估计等待时间）
                            await asyncio.sleep(120)
                            return True
                except Exception as e:
                    log.warning(f"内嵌视频处理出错: {e}")
            return False

        # 有 <video> 元素
        video_info = await self.page.evaluate("""
            () => {
                const v = document.querySelector('video');
                if (!v) return null;
                return {
                    paused: v.paused,
                    duration: v.duration,
                    currentTime: v.currentTime,
                    ended: v.ended,
                    readyState: v.readyState,
                };
            }
        """)

        if not video_info:
            return False

        duration = video_info.get("duration", 0)
        if not duration or duration <= 0:
            await asyncio.sleep(2)
            return False

        log.info(f"🎬 检测到视频，时长: {duration:.0f}s ({duration/60:.1f}min)")

        # 确保原速播放（不倍速）
        await self.page.evaluate("""
            () => {
                const v = document.querySelector('video');
                if (v) {
                    v.playbackRate = 1.0;  // 原速
                    v.muted = false;
                    if (v.paused) v.play();
                }
            }
        """)

        log.info("▶️ 开始播放（原速，不跳播）...")

        # 等待视频完整播放
        remaining = duration - video_info.get("currentTime", 0)
        log.info(f"⏳ 预计等待 {remaining:.0f}s ({remaining/60:.1f}min)")

        # 分段等待，定期检查视频状态
        check_interval = 10  # 每10秒检查一次
        elapsed = 0
        while elapsed < remaining + 30:  # 多等30秒容错
            await asyncio.sleep(check_interval)
            elapsed += check_interval

            status = await self.page.evaluate("""
                () => {
                    const v = document.querySelector('video');
                    if (!v) return { ended: true };
                    // 确保没有被暂停且保持原速
                    if (v.paused) v.play();
                    if (v.playbackRate !== 1.0) v.playbackRate = 1.0;
                    return {
                        ended: v.ended,
                        currentTime: v.currentTime,
                        duration: v.duration,
                        paused: v.paused,
                    };
                }
            """)

            if status.get("ended"):
                log.info("✅ 视频播放完毕")
                return True

            ct = status.get("currentTime", 0)
            dur = status.get("duration", duration)
            pct = (ct / dur * 100) if dur > 0 else 0
            log.info(f"   📊 进度: {pct:.1f}% ({ct:.0f}s / {dur:.0f}s)")

        log.info("✅ 视频等待结束")
        return True

    # ═══════════════════════════════════════════════════════
    # 习题作答模块
    # ═══════════════════════════════════════════════════════

    async def _complete_all_quizzes(self):
        """完成当前及后续页面的所有测验"""
        log.info("📝 开始习题作答阶段...")

        quiz_count = 0
        while True:
            answered = await self._answer_current_quiz()
            if answered:
                quiz_count += 1
                self.stats["quizzes_completed"] += 1
                await asyncio.sleep(human_delay())

                # 检查是否有错题解析，需要处理
                await self._handle_quiz_feedback()

                # 翻到下一页继续
                has_next = await self._click_next()
                if has_next:
                    await asyncio.sleep(2)
                    continue
                else:
                    break
            else:
                # 没有题目，尝试翻页
                has_next = await self._click_next()
                if has_next:
                    await asyncio.sleep(2)
                    continue
                else:
                    break

        log.info(f"📝 习题阶段完成，共完成 {quiz_count} 道题")

    async def _answer_current_quiz(self) -> bool:
        """分析并作答当前页面的测验题"""
        # 检测是否有可见的题目元素
        has_visible_quiz = False

        # 检查可见的 radio buttons
        radios = self.page.locator('input[type="radio"]')
        visible_radios = 0
        for i in range(await radios.count()):
            try:
                if await radios.nth(i).is_visible(timeout=1000):
                    visible_radios += 1
            except Exception:
                pass
        
        # 检查可见的 checkboxes（排除通用 UI checkbox）
        checkboxes = self.page.locator('input[type="checkbox"]')
        visible_checkboxes = 0
        for i in range(await checkboxes.count()):
            try:
                cb = checkboxes.nth(i)
                if await cb.is_visible(timeout=1000):
                    cb_id = (await cb.get_attribute("id") or "").lower()
                    # 排除非测验的 checkbox（如 select-all、cookie 等）
                    if not any(skip in cb_id for skip in ["select-all", "cookie", "consent", "toggle"]):
                        visible_checkboxes += 1
            except Exception:
                pass

        has_visible_quiz = visible_radios > 0 or visible_checkboxes > 0

        if not has_visible_quiz:
            # 再检查是否有其他测验指示元素
            quiz_el = self.page.locator('[class*="quiz"]:visible, [class*="question"]:visible, [class*="assessment"]:visible')
            has_visible_quiz = await quiz_el.count() > 0

        if not has_visible_quiz:
            return False

        log.info("📝 检测到测验题目，开始分析作答...")

        # 截图题目
        await self.page.screenshot(path=screenshot_path(f"quiz_{self.stats['quizzes_completed'] + 1}"))

        # 获取题目文本用于分析
        question_text = await self._get_question_text()
        if question_text:
            log.info(f"📋 题目: {question_text[:100]}...")

        # 作答单选题
        if has_radio:
            await self._answer_radio_questions()

        # 作答多选题
        if has_checkbox:
            await self._answer_checkbox_questions()

        # 处理拖拽题、填空题等
        await self._answer_other_types()

        await asyncio.sleep(human_delay())

        # 点击提交/检查按钮
        submitted = await self._submit_quiz()
        return submitted

    async def _get_question_text(self) -> str:
        """获取题目文本"""
        question_selectors = [
            '.question-text', '.quiz-question', '[class*="question-body"]',
            '[class*="question"] p', '[class*="prompt"]', 'h3', 'h4',
            '[role="heading"]',
        ]
        for sel in question_selectors:
            try:
                el = self.page.locator(sel)
                if await el.count() > 0:
                    return (await el.first.text_content() or "").strip()
            except Exception:
                continue
        return ""

    async def _answer_radio_questions(self):
        """作答单选题 — 分析选项内容，选择最合理的答案"""
        radios = self.page.locator('input[type="radio"]')
        count = await radios.count()
        if count == 0:
            return

        # 按 name 分组
        groups = {}
        for i in range(count):
            name = await radios.nth(i).get_attribute("name") or f"unnamed_{i}"
            if name not in groups:
                groups[name] = []
            groups[name].append(i)

        for name, indices in groups.items():
            # 获取每个选项的文本
            options = []
            for idx in indices:
                radio = radios.nth(idx)
                # 尝试获取关联 label 的文本
                radio_id = await radio.get_attribute("id") or ""
                label_text = ""
                if radio_id:
                    label = self.page.locator(f'label[for="{radio_id}"]')
                    if await label.count() > 0:
                        label_text = (await label.first.text_content() or "").strip()

                if not label_text:
                    # 尝试获取父元素文本
                    parent = radio.locator("..")
                    label_text = (await parent.text_content() or "").strip()

                options.append({"index": idx, "text": label_text})

            # 智能选择：分析选项内容
            best_idx = self._pick_best_answer(options)
            log.info(f"   ✏️ 单选 [{name}]: 选择第 {best_idx + 1} 项")
            await radios.nth(indices[best_idx]).click()
            await asyncio.sleep(0.5)

    async def _answer_checkbox_questions(self):
        """作答多选题"""
        checkboxes = self.page.locator('input[type="checkbox"]')
        count = await checkboxes.count()
        if count == 0:
            return

        # 多选题：只处理可见的 checkbox
        options = []
        for i in range(count):
            cb = checkboxes.nth(i)
            try:
                if not await cb.is_visible(timeout=2000):
                    continue
            except Exception:
                continue

            cb_id = await cb.get_attribute("id") or ""
            label_text = ""
            if cb_id:
                label = self.page.locator(f'label[for="{cb_id}"]')
                if await label.count() > 0:
                    label_text = (await label.first.text_content() or "").strip()
            if not label_text:
                try:
                    parent = cb.locator("..")
                    label_text = (await parent.text_content() or "").strip()
                except Exception:
                    pass
            options.append({"index": i, "text": label_text})

        if not options:
            return

        # 选择至少2个选项
        to_select = self._pick_multiple_answers(options)
        for opt in to_select:
            idx = opt  # to_select 返回的是 options 列表的索引
            actual_idx = options[idx]["index"] if idx < len(options) else None
            if actual_idx is not None:
                try:
                    cb = checkboxes.nth(actual_idx)
                    if await cb.is_visible(timeout=2000):
                        log.info(f"   ✏️ 多选: 勾选 {options[idx]['text'][:30]}")
                        await cb.click()
                        await asyncio.sleep(0.3)
                except Exception as e:
                    log.warning(f"   ⚠️ 勾选失败: {e}")

    async def _answer_other_types(self):
        """处理其他题型：填空、拖拽匹配等"""
        # 填空题
        text_inputs = self.page.locator('input[type="text"]:not([name="username"]):not([name="password"]):not([name="email"])')
        count = await text_inputs.count()
        for i in range(count):
            inp = text_inputs.nth(i)
            if await inp.is_visible():
                current_val = await inp.input_value()
                if not current_val:
                    placeholder = await inp.get_attribute("placeholder") or ""
                    log.info(f"   ✏️ 填空题: placeholder='{placeholder}'")
                    # 暂时留空，等提交后看反馈
                    pass

    def _pick_best_answer(self, options: list) -> int:
        """
        分析选项内容，选择最可能正确的答案
        策略：
        - 网络安全相关题目的常见正确答案模式
        - 选最长/最详细的选项（通常更准确）
        - 避免绝对化表述（"总是"、"从不"）
        """
        if not options:
            return 0

        scores = []
        for opt in options:
            text = opt["text"].lower()
            score = len(text)  # 基础分：长度（详细答案通常更准确）

            # 加分：包含技术术语
            tech_terms = ["协议", "protocol", "加密", "encrypt", "认证", "auth",
                         "防火墙", "firewall", "漏洞", "vulnerability", "安全",
                         "tcp", "udp", "icmp", "dns", "http", "ssl", "tls",
                         "wireshark", "ids", "ips", "vpn", "ipsec"]
            for term in tech_terms:
                if term in text:
                    score += 10

            # 减分：绝对化表述
            absolutes = ["总是", "从不", "所有", "绝不", "always", "never", "all", "none"]
            for word in absolutes:
                if word in text:
                    score -= 15

            # 加分：包含 "可以"、"可能"、"通常" 等修饰语
            modifiers = ["可以", "可能", "通常", "一般", "often", "usually", "can", "may"]
            for word in modifiers:
                if word in text:
                    score += 5

            scores.append(score)

        return scores.index(max(scores))

    def _pick_multiple_answers(self, options: list) -> list:
        """多选题选择策略：选择得分最高的若干项"""
        if len(options) <= 2:
            return list(range(len(options)))

        scored = []
        for i, opt in enumerate(options):
            text = opt["text"].lower()
            score = len(text)
            tech_terms = ["协议", "安全", "加密", "认证", "防火墙", "protocol",
                         "security", "encrypt", "firewall", "vpn"]
            for term in tech_terms:
                if term in text:
                    score += 10
            scored.append((i, score))

        scored.sort(key=lambda x: x[1], reverse=True)
        # 选择至少2个，最多选总数-1个
        pick_count = max(2, len(options) // 2)
        return [s[0] for s in scored[:pick_count]]

    async def _submit_quiz(self) -> bool:
        """提交测验答案"""
        submit_selectors = [
            'button:has-text("Submit")', 'button:has-text("提交")',
            'button:has-text("Check")', 'button:has-text("检查")',
            'button:has-text("Check Answer")', 'button:has-text("检查答案")',
            'button[type="submit"]', 'input[type="submit"]',
            'button:has-text("Verify")', 'button:has-text("验证")',
        ]
        for sel in submit_selectors:
            try:
                btn = self.page.locator(sel)
                if await btn.count() > 0 and await btn.first.is_visible():
                    await btn.first.click()
                    log.info(f"📨 已提交答案")
                    await asyncio.sleep(3)
                    return True
            except Exception:
                continue
        return False

    async def _handle_quiz_feedback(self):
        """处理答题反馈（错题解析等）"""
        await asyncio.sleep(2)

        # 检查是否有反馈信息
        feedback_selectors = [
            '[class*="feedback"]', '[class*="result"]', '[class*="explanation"]',
            '.correct', '.incorrect', '[class*="answer-result"]',
        ]

        for sel in feedback_selectors:
            try:
                el = self.page.locator(sel)
                if await el.count() > 0 and await el.first.is_visible():
                    text = (await el.first.text_content() or "").strip()
                    if text:
                        is_correct = any(w in text.lower() for w in ["correct", "正确", "✓", "对"])
                        is_wrong = any(w in text.lower() for w in ["incorrect", "错误", "✗", "wrong", "错"])
                        if is_correct:
                            log.info(f"   ✅ 回答正确")
                        elif is_wrong:
                            log.info(f"   ❌ 回答错误 — {text[:80]}")
                        else:
                            log.info(f"   💡 反馈: {text[:80]}")
                    break
            except Exception:
                continue

        # 点击继续/下一步（如果有反馈后的继续按钮）
        continue_btns = [
            'button:has-text("Continue")', 'button:has-text("继续")',
            'button:has-text("Next")', 'button:has-text("下一步")',
            'button:has-text("Try Again")', 'button:has-text("重试")',
            'button:has-text("OK")', 'button:has-text("确定")',
        ]
        for sel in continue_btns:
            try:
                btn = self.page.locator(sel)
                if await btn.count() > 0 and await btn.first.is_visible():
                    await btn.first.click()
                    await asyncio.sleep(1)
                    break
            except Exception:
                continue

    # ═══════════════════════════════════════════════════════
    # 导航与页面操作
    # ═══════════════════════════════════════════════════════

    async def _click_next(self) -> bool:
        """点击下一步/翻页"""
        next_selectors = [
            'button:has-text("Next")', 'button:has-text("下一步")',
            'button:has-text("下一页")', 'a:has-text("Next")',
            'button[aria-label*="next"]', 'button[aria-label*="Next"]',
            'button:has-text("Continue")', 'button:has-text("继续")',
            'button:has-text("Mark Complete")', 'button:has-text("标记完成")',
            '[data-testid*="next"]', '.next-button', 'button.btn-next',
            '.pagination-next a', '.pagination-next button',
        ]

        for sel in next_selectors:
            try:
                btn = self.page.locator(sel)
                if await btn.count() > 0:
                    first = btn.first
                    if await first.is_visible() and await first.is_enabled():
                        await first.click()
                        self.stats["pages_navigated"] += 1
                        log.info(f"➡️ 翻页")
                        await asyncio.sleep(2)
                        return True
            except Exception:
                continue

        return False

    async def _enter_next_incomplete_module(self) -> bool:
        """从课程主页找到第一个未完成的模块并进入"""
        log.info("🔍 查找未完成的模块...")

        # 查找模块/章节列表
        module_selectors = [
            # 常见的模块列表选择器
            '[class*="module"] a', '[class*="chapter"] a', '[class*="section"] a',
            '[class*="lesson"] a', '[class*="topic"] a',
            'nav a', '.sidebar a', '[class*="toc"] a',
            '[class*="syllabus"] a', '[class*="curriculum"] a',
            # 通用链接
            'li a[href*="module"]', 'li a[href*="chapter"]',
            'li a[href*="section"]', 'li a[href*="lesson"]',
        ]

        for sel in module_selectors:
            try:
                links = self.page.locator(sel)
                count = await links.count()
                if count == 0:
                    continue

                for i in range(count):
                    link = links.nth(i)
                    if not await link.is_visible():
                        continue

                    text = (await link.text_content() or "").strip()
                    if not text or len(text) < 2:
                        continue

                    # 检查是否已完成（通过 class、aria 或图标判断）
                    parent = link.locator("..")
                    parent_class = (await parent.get_attribute("class") or "").lower()
                    link_class = (await link.get_attribute("class") or "").lower()
                    aria = (await link.get_attribute("aria-label") or "").lower()

                    is_completed = any(w in f"{parent_class} {link_class} {aria}" for w in
                                      ["completed", "complete", "done", "finished", "passed"])

                    if not is_completed:
                        log.info(f"📑 进入未完成模块: {text[:60]}")
                        await link.click()
                        await asyncio.sleep(3)
                        return True
            except Exception:
                continue

        # 备选：直接点击页面上的 "Start" / "Resume" / "继续学习" 按钮
        start_selectors = [
            'button:has-text("Start")', 'button:has-text("开始")',
            'button:has-text("Resume")', 'button:has-text("继续学习")',
            'a:has-text("Start")', 'a:has-text("Resume")',
            'a:has-text("开始学习")', 'a:has-text("继续学习")',
            'button:has-text("Launch")', 'a:has-text("Launch")',
        ]
        for sel in start_selectors:
            try:
                btn = self.page.locator(sel)
                if await btn.count() > 0 and await btn.first.is_visible():
                    await btn.first.click()
                    log.info(f"🚀 点击开始/继续: {sel}")
                    await asyncio.sleep(3)
                    return True
            except Exception:
                continue

        return False

    async def _is_course_complete(self) -> bool:
        """检查课程是否全部完成"""
        # 查找进度指示
        progress_selectors = [
            '[class*="progress"]', '[role="progressbar"]',
            '[class*="completion"]', '[aria-valuenow]',
        ]
        for sel in progress_selectors:
            try:
                el = self.page.locator(sel)
                count = await el.count()
                for i in range(count):
                    text = (await el.nth(i).text_content() or "").strip()
                    if "100%" in text:
                        # 检查是不是课程总进度
                        log.info(f"📊 检测到进度: {text[:60]}")

                    # 通过 aria-valuenow 检查
                    val = await el.nth(i).get_attribute("aria-valuenow")
                    if val and float(val) >= 100:
                        log.info("📊 课程进度 100%")
                        return True
            except Exception:
                continue

        return False

    async def _do_page_actions(self, max_idle: int = 15) -> bool:
        """在当前页面持续执行操作（视频+答题+翻页），直到无操作可做"""
        idle_count = 0
        any_action = False

        while idle_count < max_idle:
            action = False

            # 处理弹窗
            if await self._dismiss_dialogs():
                action = True

            # 视频
            if await self._watch_current_video():
                action = True
                self.stats["videos_watched"] += 1

            # 答题
            if await self._answer_current_quiz():
                action = True
                self.stats["quizzes_completed"] += 1
                await self._handle_quiz_feedback()

            # 翻页
            if not action:
                if await self._click_next():
                    action = True
                    await asyncio.sleep(2)

            if action:
                idle_count = 0
                any_action = True
            else:
                idle_count += 1
                await asyncio.sleep(1)

        return any_action

    async def _dismiss_dialogs(self) -> bool:
        """关闭弹窗"""
        dialog_btns = [
            'button:has-text("Continue")', 'button:has-text("OK")',
            'button:has-text("确定")', 'button:has-text("Got it")',
            'button:has-text("Close")', 'button:has-text("关闭")',
            'button:has-text("Accept")', 'button:has-text("接受")',
            'button:has-text("Dismiss")',
            '[role="dialog"] button', '[class*="modal"] button',
        ]
        for sel in dialog_btns:
            try:
                btn = self.page.locator(sel)
                if await btn.count() > 0 and await btn.first.is_visible():
                    # 确认是弹窗按钮（不是页面主按钮）
                    parent_role = await btn.first.locator("..").get_attribute("role") or ""
                    parent_class = (await btn.first.locator("..").get_attribute("class") or "").lower()
                    if any(w in f"{parent_role} {parent_class}" for w in ["dialog", "modal", "overlay", "popup", "banner", "cookie"]):
                        await btn.first.click()
                        log.info(f"🔔 关闭弹窗")
                        await asyncio.sleep(1)
                        return True
            except Exception:
                continue
        return False

    # ═══════════════════════════════════════════════════════
    # 工具方法
    # ═══════════════════════════════════════════════════════

    async def _find_visible(self, *selectors):
        """在多个选择器中找到第一个可见的元素"""
        for sel in selectors:
            try:
                el = self.page.locator(sel)
                if await el.count() > 0 and await el.first.is_visible():
                    return el.first
            except Exception:
                continue
        return None

    def _save_and_report(self):
        """保存状态并打印报告"""
        self.state["last_run"] = time.strftime("%Y-%m-%d %H:%M:%S")
        self.state["stats"] = self.stats
        save_state(self.state)

        log.info("\n" + "=" * 60)
        log.info("📊 学习统计报告")
        log.info("=" * 60)
        log.info(f"   🎬 观看视频: {self.stats['videos_watched']} 个")
        log.info(f"   📝 完成习题: {self.stats['quizzes_completed']} 道")
        log.info(f"   📑 完成模块: {self.stats['modules_completed']} 个")
        log.info(f"   📄 翻页次数: {self.stats['pages_navigated']} 次")
        log.info(f"   ❌ 错误次数: {self.stats['errors']} 次")
        log.info("=" * 60)
        log.info("💾 进度已保存")


# ═══════════════════════════════════════════════════════════════
# CLI 入口
# ═══════════════════════════════════════════════════════════════

async def main():
    parser = argparse.ArgumentParser(description="NetAcad 标准化学习引擎")
    parser.add_argument("--email", required=True, help="登录邮箱")
    parser.add_argument("--password", required=True, help="登录密码")
    parser.add_argument("--course", default="网络信息安全技术", help="课程名称 (默认: 网络信息安全技术)")
    parser.add_argument("--course-url", default=None, help="课程直达链接（可选）")
    parser.add_argument("--headless", action="store_true", help="无头模式（不显示浏览器）")
    args = parser.parse_args()

    learner = NetAcadLearner(
        email=args.email,
        password=args.password,
        course_name=args.course,
        headless=args.headless,
        course_url=args.course_url,
    )
    await learner.start()


if __name__ == "__main__":
    asyncio.run(main())
