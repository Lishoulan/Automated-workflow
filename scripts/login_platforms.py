"""交互式登录脚本 — 逐个登录微博/知乎/小红书，保存浏览器profile。"""

import os
import sys
import time
from pathlib import Path

# 确保项目根目录在 sys.path 中
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if project_root not in sys.path:
    sys.path.insert(0, project_root)
src_dir = os.path.join(project_root, "src")
if src_dir not in sys.path:
    sys.path.insert(0, src_dir)

os.environ.setdefault("PROJECT_ROOT", project_root)


def _init_settings():
    from toutiao_content_factory.config import load_settings
    return load_settings(Path(project_root), account_key="robot_notes")


def login_weibo(settings):
    """登录微博。"""
    from toutiao_content_factory.services.weibo_publisher import WeiboPublisher
    import logging
    logger = logging.getLogger("login.weibo")
    publisher = WeiboPublisher(settings, logger=logger)
    print("\n" + "=" * 60)
    print("  微博登录")
    print("=" * 60)
    result = publisher.login_interactive()
    print(f"微博登录结果: {result}")
    return result.get("success", False)


def login_zhihu(settings):
    """登录知乎。"""
    from toutiao_content_factory.services.zhihu_publisher import ZhihuPublisher
    import logging
    logger = logging.getLogger("login.zhihu")
    publisher = ZhihuPublisher(settings, logger=logger)

    if not hasattr(publisher, "login_interactive"):
        # 如果知乎发布器没有 login_interactive，手动实现
        return _manual_login(publisher, settings, "知乎", "https://www.zhihu.com/signin", logger)

    print("\n" + "=" * 60)
    print("  知乎登录")
    print("=" * 60)
    result = publisher.login_interactive()
    print(f"知乎登录结果: {result}")
    return result.get("success", False)


def login_xiaohongshu(settings):
    """登录小红书。"""
    from toutiao_content_factory.services.xiaohongshu_publisher import XiaohongshuPublisher
    import logging
    logger = logging.getLogger("login.xiaohongshu")
    publisher = XiaohongshuPublisher(settings, logger=logger)

    if hasattr(publisher, "login_interactive"):
        print("\n" + "=" * 60)
        print("  小红书登录")
        print("=" * 60)
        result = publisher.login_interactive()
        print(f"小红书登录结果: {result}")
        return result.get("success", False)

    # 手动实现
    return _manual_login(publisher, settings, "小红书", "https://www.xiaohongshu.com", logger)


def _manual_login(publisher, settings, platform_name, login_url, logger):
    """通用的手动登录流程。"""
    from playwright.sync_api import sync_playwright
    from toutiao_content_factory.services.browser_launch_guard import prepare_browser_profile

    profile_dir = publisher._resolve_browser_profile_dir()
    profile_dir.mkdir(parents=True, exist_ok=True)

    prepare_browser_profile(
        profile_dir=profile_dir,
        project_root=settings.project_root,
        logger=logger,
        label=f"{platform_name}:interactive-login",
        extra_paths=[settings.account_runtime_dir],
    )

    print("\n" + "=" * 60)
    print(f"  {platform_name}登录")
    print("=" * 60)

    with sync_playwright() as playwright:
        channel = settings.publishing.channel or None
        executable_path = settings.publishing.executable_path or None

        launch_kwargs = {
            "user_data_dir": str(profile_dir),
            "headless": False,
        }
        if channel:
            launch_kwargs["channel"] = channel
        if executable_path:
            launch_kwargs["executable_path"] = executable_path

        browser = playwright.chromium.launch_persistent_context(**launch_kwargs)
        try:
            page = browser.pages[0] if browser.pages else browser.new_page()
            page.goto(login_url, wait_until="domcontentloaded", timeout=15000)

            print(f"\n请在打开的浏览器窗口中完成{platform_name}登录。")
            print("登录成功后请在此终端按 Enter 键确认...")

            # 等待用户按 Enter 确认
            input(f"\n{platform_name}登录完成后按 Enter 继续...")

            # 验证：刷新页面看是否还在登录页
            current_url = page.url
            print(f"当前页面 URL: {current_url}")

            return True

        except Exception as exc:
            print(f"{platform_name}登录异常: {exc}")
            return False
        finally:
            browser.close()


def main():
    import logging
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    settings = _init_settings()

    platforms = []
    if len(sys.argv) > 1:
        platforms = [a.lower() for a in sys.argv[1:]]
    else:
        platforms = ["weibo", "zhihu", "xiaohongshu"]

    results = {}

    for platform in platforms:
        if platform in ("weibo", "微博"):
            results["微博"] = login_weibo(settings)
        elif platform in ("zhihu", "知乎"):
            results["知乎"] = login_zhihu(settings)
        elif platform in ("xiaohongshu", "小红书", "xhs"):
            results["小红书"] = login_xiaohongshu(settings)
        else:
            print(f"未知平台: {platform}")

    print("\n" + "=" * 60)
    print("  登录结果汇总")
    print("=" * 60)
    for name, success in results.items():
        status = "✓ 成功" if success else "✗ 失败"
        print(f"  {name}: {status}")

    # 检查profile目录
    print("\n浏览器profile保存位置:")
    from toutiao_content_factory.services.weibo_publisher import WeiboPublisher
    from toutiao_content_factory.services.zhihu_publisher import ZhihuPublisher
    from toutiao_content_factory.services.xiaohongshu_publisher import XiaohongshuPublisher

    for name, cls in [("微博", WeiboPublisher), ("知乎", ZhihuPublisher), ("小红书", XiaohongshuPublisher)]:
        pub = cls(settings)
        pdir = pub._resolve_browser_profile_dir()
        exists = "存在" if pdir.exists() else "不存在"
        print(f"  {name}: {pdir} ({exists})")


if __name__ == "__main__":
    main()
