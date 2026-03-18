"""Login helper for xhs playwright publish mode."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app.config import load_settings


def main() -> None:
    settings = load_settings()
    try:
        from playwright.sync_api import sync_playwright
    except Exception as exc:  # pragma: no cover
        raise RuntimeError(f"Playwright 不可用: {exc}") from exc

    user_data_dir = Path(settings.xhs_user_data_dir).resolve()
    user_data_dir.mkdir(parents=True, exist_ok=True)

    print(f"浏览器数据目录: {user_data_dir}")
    print("即将打开小红书创作中心，请在浏览器中手动登录。")
    print("登录完成后，回终端按回车，保存登录态。")

    with sync_playwright() as p:
        launch_kwargs = {
            "user_data_dir": str(user_data_dir),
            "headless": False,
        }
        if settings.xhs_proxy_server:
            proxy_cfg = {"server": settings.xhs_proxy_server}
            if settings.xhs_proxy_username:
                proxy_cfg["username"] = settings.xhs_proxy_username
            if settings.xhs_proxy_password:
                proxy_cfg["password"] = settings.xhs_proxy_password
            launch_kwargs["proxy"] = proxy_cfg
        context = p.chromium.launch_persistent_context(**launch_kwargs)
        page = context.new_page()
        try:
            page.goto(
                settings.xhs_creator_url,
                wait_until="domcontentloaded",
                timeout=max(settings.xhs_wait_timeout_ms, 10000),
            )
        except Exception as exc:
            print(f"自动打开创作中心失败: {exc}")
            print(f"请在已打开的浏览器里手动访问: {settings.xhs_creator_url}")
        input("登录完成后按回车退出...\n")
        try:
            context.close()
        except Exception:
            # 可能用户已手动关闭浏览器窗口，此时忽略关闭异常
            pass

    print("登录态已保存。")


if __name__ == "__main__":
    main()
