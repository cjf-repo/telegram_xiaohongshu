"""XHS publisher adapters."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import List
from uuid import uuid4


@dataclass
class XHSPublishResult:
    """Publish result."""

    success: bool
    note_id: str | None = None
    note_url: str | None = None
    error: str | None = None


class PlaywrightXHSPublisher:
    """Publish note by playwright."""

    def __init__(
        self,
        *,
        creator_url: str,
        user_data_dir: str,
        auto_click_publish: bool,
        publish_button_text: str,
        wait_timeout_ms: int,
        proxy_server: str = "",
        proxy_username: str = "",
        proxy_password: str = "",
    ):
        self.creator_url = creator_url
        self.user_data_dir = str(Path(user_data_dir).resolve())
        self.auto_click_publish = auto_click_publish
        self.publish_button_text = publish_button_text or "发布"
        self.wait_timeout_ms = max(int(wait_timeout_ms), 10000)
        self.proxy_server = (proxy_server or "").strip()
        self.proxy_username = (proxy_username or "").strip()
        self.proxy_password = (proxy_password or "").strip()
        self._debug_dir = Path(self.user_data_dir) / "_publish_debug"
        self._debug_dir.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _is_login_required(page) -> bool:
        url = page.url.lower()
        if "login" in url or "passport" in url:
            return True
        login_hints = ["登录", "手机号登录", "验证码登录", "扫码登录"]
        try:
            page_text = page.locator("body").inner_text(timeout=5000)
        except Exception:
            return False
        return any(hint in page_text for hint in login_hints)

    @staticmethod
    def _all_frames(page):
        frames = []
        try:
            frames.extend(page.frames)
        except Exception:
            pass
        if not frames:
            try:
                frames.append(page.main_frame)
            except Exception:
                pass
        return frames

    @classmethod
    def _fill_title_and_body(cls, page, title: str, body: str) -> None:
        frames = cls._all_frames(page)

        title_filled = False
        for frame in frames:
            title_candidates = [
                frame.get_by_placeholder("填写标题会有更多赞哦"),
                frame.locator("input[placeholder*='标题']"),
                frame.locator("textarea[placeholder*='标题']"),
                frame.locator("input[placeholder*='写个标题']"),
                frame.locator("textarea[placeholder*='写个标题']"),
                frame.locator("input[placeholder*='添加标题']"),
                frame.locator("textarea[placeholder*='添加标题']"),
            ]
            for locator in title_candidates:
                try:
                    if locator.count() > 0:
                        locator.first.fill(title)
                        title_filled = True
                        break
                except Exception:
                    continue
            if title_filled:
                break
        body_to_fill = body
        if not title_filled and title:
            # 某些发布页没有独立标题框，兜底把标题拼到正文开头
            body_to_fill = f"{title}\n\n{body}".strip()

        body_filled = False
        for frame in frames:
            body_candidates = [
                frame.locator("div[contenteditable='true']").first,
                frame.get_by_role("textbox").first,
                frame.locator("textarea[placeholder*='正文']").first,
                frame.locator("textarea[placeholder*='描述']").first,
                frame.locator("div[contenteditable='true'][data-placeholder*='正文']").first,
            ]
            for locator in body_candidates:
                try:
                    if locator.count() > 0:
                        locator.fill(body_to_fill)
                        body_filled = True
                        break
                except Exception:
                    continue
            if body_filled:
                break
        if not body_filled:
            raise RuntimeError("未找到小红书正文输入框，请更新选择器")

    def _save_debug_screenshot(self, page, tag: str) -> str:
        file_name = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{tag}.png"
        out_file = self._debug_dir / file_name
        try:
            page.screenshot(path=str(out_file), full_page=True)
        except Exception:
            try:
                page.screenshot(path=str(out_file))
            except Exception:
                return ""
        return str(out_file)

    @classmethod
    def _collect_file_inputs(cls, page):
        results = []
        for frame in cls._all_frames(page):
            try:
                locator = frame.locator("input[type='file']")
                count = min(locator.count(), 20)
            except Exception:
                continue
            for idx in range(count):
                item = locator.nth(idx)
                try:
                    accept = (item.get_attribute("accept") or "").lower()
                    multiple = item.get_attribute("multiple") is not None
                except Exception:
                    accept = ""
                    multiple = False
                results.append({"locator": item, "accept": accept, "multiple": multiple})
        return results

    @staticmethod
    def _is_image_file(path: str) -> bool:
        ext = Path(path).suffix.lower()
        return ext in {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"}

    @staticmethod
    def _is_video_file(path: str) -> bool:
        ext = Path(path).suffix.lower()
        return ext in {".mp4", ".mov", ".mkv", ".flv", ".m4v", ".avi", ".mpeg", ".mpg", ".ts"}

    @classmethod
    def _infer_publish_kind(cls, media_paths: List[str]) -> str:
        image_count = sum(1 for p in media_paths if cls._is_image_file(p))
        video_count = sum(1 for p in media_paths if cls._is_video_file(p))
        if video_count > 0 and image_count == 0:
            return "video"
        return "image"

    @staticmethod
    def _is_image_accept(accept: str) -> bool:
        return "image" in accept or any(x in accept for x in [".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"])

    @staticmethod
    def _is_video_accept(accept: str) -> bool:
        return "video" in accept or any(x in accept for x in [".mp4", ".mov", ".mkv", ".flv", ".m4v", ".avi", ".mpeg", ".mpg", ".ts"])

    @classmethod
    def _accept_matches_kind(cls, accept: str, target_kind: str) -> bool:
        if not accept:
            return True
        is_image = cls._is_image_accept(accept)
        is_video = cls._is_video_accept(accept)
        if target_kind == "image":
            if is_video and not is_image:
                return False
            return True
        if target_kind == "video":
            if is_image and not is_video:
                return False
            return True
        return True

    @classmethod
    def _find_best_file_input(cls, page, media_paths: List[str], target_kind: str):
        candidates = cls._collect_file_inputs(page)
        if not candidates:
            return None

        image_count = sum(1 for p in media_paths if cls._is_image_file(p))
        prefer_image = image_count >= max(1, len(media_paths) - image_count)

        best = None
        best_score = -10**9
        for item in candidates:
            accept = item["accept"]
            multiple = item["multiple"]
            score = 0

            kind_matched = cls._accept_matches_kind(accept, target_kind)
            if kind_matched:
                score += 10
            else:
                score -= 50

            if multiple:
                score += 2
            if not accept:
                score += 1

            if prefer_image:
                if "image" in accept or any(x in accept for x in [".jpg", ".jpeg", ".png", ".webp"]):
                    score += 8
                if "video" in accept or any(x in accept for x in [".mp4", ".mov", ".mkv", ".flv"]):
                    score -= 6
            else:
                if "video" in accept or any(x in accept for x in [".mp4", ".mov", ".mkv", ".flv"]):
                    score += 8
                if "image" in accept:
                    score -= 4

            if score > best_score:
                best_score = score
                best = item
                best["score"] = score
                best["kind_matched"] = kind_matched
        return best

    @classmethod
    def _wait_file_input(cls, page, timeout_ms: int) -> bool:
        start = datetime.now().timestamp()
        timeout_s = max(timeout_ms, 2000) / 1000.0
        while datetime.now().timestamp() - start < timeout_s:
            if cls._collect_file_inputs(page):
                return True
            try:
                page.wait_for_timeout(500)
            except Exception:
                break
        return False

    @classmethod
    def _has_matching_input(cls, page, media_paths: List[str], target_kind: str) -> bool:
        best_input = cls._find_best_file_input(page, media_paths, target_kind)
        return bool(best_input and best_input.get("kind_matched"))

    @classmethod
    def _click_first_visible(cls, locator, timeout_ms: int = 1800) -> bool:
        try:
            count = min(locator.count(), 8)
        except Exception:
            return False
        for idx in range(count):
            item = locator.nth(idx)
            try:
                if not item.is_visible(timeout=400):
                    continue
                item.click(timeout=timeout_ms)
                return True
            except Exception:
                continue
        return False

    @classmethod
    def _click_text_in_frames(cls, page, text: str) -> bool:
        for frame in cls._all_frames(page):
            for role in ["tab", "button", "link"]:
                try:
                    target = frame.get_by_role(role, name=text, exact=True)
                    if cls._click_first_visible(target):
                        return True
                except Exception:
                    continue
                try:
                    fuzzy_target = frame.get_by_role(role, name=text, exact=False)
                    if cls._click_first_visible(fuzzy_target):
                        return True
                except Exception:
                    continue
            try:
                text_target = frame.get_by_text(text, exact=False)
                if cls._click_first_visible(text_target):
                    return True
            except Exception:
                continue
        return False

    def _open_publish_menu(self, page) -> bool:
        menu_labels = ["发布笔记", "去发布", "开始发布"]
        for label in menu_labels:
            if self._click_text_in_frames(page, label):
                try:
                    page.wait_for_timeout(500)
                except Exception:
                    pass
                return True
        return False

    @classmethod
    def _wait_matching_input(
        cls, page, media_paths: List[str], target_kind: str, timeout_ms: int
    ) -> bool:
        start = datetime.now().timestamp()
        timeout_s = max(timeout_ms, 2000) / 1000.0
        while datetime.now().timestamp() - start < timeout_s:
            if cls._wait_file_input(page, 1200):
                best_input = cls._find_best_file_input(page, media_paths, target_kind)
                if best_input and best_input.get("kind_matched"):
                    return True
            try:
                page.wait_for_timeout(350)
            except Exception:
                break
        return False

    @staticmethod
    def _dismiss_overlays(page) -> None:
        try:
            page.keyboard.press("Escape")
            page.wait_for_timeout(150)
            page.keyboard.press("Escape")
            page.wait_for_timeout(150)
        except Exception:
            pass

    def _switch_publish_kind(self, page, target_kind: str, include_generic: bool = False) -> bool:
        if target_kind == "video":
            preferred = ["上传视频", "发布视频", "视频笔记", "视频"]
        else:
            preferred = ["上传图文", "发布图文", "图文笔记", "图文"]

        clicked = False
        for text in preferred:
            if self._click_text_in_frames(page, text):
                clicked = True
                break

        if not clicked and self._open_publish_menu(page):
            for text in preferred:
                if self._click_text_in_frames(page, text):
                    clicked = True
                    break

        if (not clicked) and include_generic:
            generic = ["发布", "发布笔记", "去发布", "开始发布"]
            for text in generic:
                if self._click_text_in_frames(page, text):
                    break
        try:
            page.wait_for_timeout(700)
        except Exception:
            pass
        return clicked

    def _ensure_publish_editor(self, page, media_paths: List[str], target_kind: str) -> bool:
        """Enter editor with low-refresh and low-reload strategy."""

        def _try_switch_once(wait_ms: int, include_generic: bool = False) -> bool:
            self._dismiss_overlays(page)
            clicked = self._switch_publish_kind(page, target_kind, include_generic=include_generic)
            if not clicked and not include_generic:
                return False
            return self._wait_matching_input(page, media_paths, target_kind, wait_ms)

        def _is_publish_like_url() -> bool:
            try:
                url = (page.url or "").lower()
            except Exception:
                return False
            return "creator.xiaohongshu.com/publish" in url

        # 1) 先在当前页尝试，不跳转
        if self._has_matching_input(page, media_paths, target_kind):
            return True

        # 2) 当前页精确切换（不使用通用入口）
        if _try_switch_once(8000, include_generic=False):
            return True

        # 3) 当前页再试一次（仍然不刷新）
        if _try_switch_once(7000, include_generic=False):
            return True

        # 4) 如当前不在发布页，先跳转配置的发布页；在发布页则不跳转
        if not _is_publish_like_url():
            try:
                page.goto(self.creator_url, wait_until="domcontentloaded", timeout=self.wait_timeout_ms)
            except Exception:
                pass
        if self._has_matching_input(page, media_paths, target_kind):
            return True
        if _try_switch_once(9000, include_generic=False):
            return True

        # 5) 标准发布地址 + 通用入口兜底
        try:
            page.goto(
                "https://creator.xiaohongshu.com/publish/publish",
                wait_until="domcontentloaded",
                timeout=self.wait_timeout_ms,
            )
        except Exception:
            pass
        if self._has_matching_input(page, media_paths, target_kind):
            return True
        if _try_switch_once(10000, include_generic=True):
            return True

        # 6) 最后才使用 reload（减少你看到的首屏刷新）
        try:
            page.reload(wait_until="domcontentloaded", timeout=self.wait_timeout_ms)
        except Exception:
            pass
        if self._has_matching_input(page, media_paths, target_kind):
            return True
        if _try_switch_once(10000, include_generic=True):
            return True

        # 7) 手动兜底：等待用户手动点“图文/视频”后继续
        if self._wait_matching_input(page, media_paths, target_kind, 30000):
            return True
        return False

    @classmethod
    def _has_text_editor(cls, page) -> bool:
        selectors = [
            "input[placeholder*='标题']",
            "textarea[placeholder*='标题']",
            "input[placeholder*='写个标题']",
            "textarea[placeholder*='写个标题']",
            "input[placeholder*='添加标题']",
            "textarea[placeholder*='添加标题']",
            "textarea[placeholder*='正文']",
            "textarea[placeholder*='描述']",
            "div[contenteditable='true'][data-placeholder*='正文']",
            "div[contenteditable='true']",
            "textarea",
        ]
        for frame in cls._all_frames(page):
            for selector in selectors:
                try:
                    locator = frame.locator(selector)
                    if locator.count() <= 0:
                        continue
                    if locator.first.is_visible(timeout=600):
                        return True
                except Exception:
                    continue
        return False

    @classmethod
    def _wait_text_editor(cls, page, timeout_ms: int) -> bool:
        start = datetime.now().timestamp()
        timeout_s = max(timeout_ms, 2000) / 1000.0
        while datetime.now().timestamp() - start < timeout_s:
            if cls._has_text_editor(page):
                return True
            try:
                page.wait_for_timeout(500)
            except Exception:
                break
        return False

    def publish(
        self,
        *,
        title: str,
        body: str,
        media_paths: List[str],
    ) -> XHSPublishResult:
        try:
            from playwright.sync_api import sync_playwright
        except Exception as exc:  # pragma: no cover
            return XHSPublishResult(success=False, error=f"Playwright 不可用: {exc}")

        existing_paths = [str(Path(p).resolve()) for p in media_paths if p]
        missing = [p for p in existing_paths if not Path(p).exists()]
        if missing:
            return XHSPublishResult(success=False, error=f"媒体文件不存在: {missing[:3]}")
        if not existing_paths:
            return XHSPublishResult(success=False, error="没有可发布的媒体文件")
        target_kind = self._infer_publish_kind(existing_paths)

        Path(self.user_data_dir).mkdir(parents=True, exist_ok=True)

        try:
            with sync_playwright() as p:
                launch_kwargs = {
                    "user_data_dir": self.user_data_dir,
                    "headless": False,
                }
                if self.proxy_server:
                    proxy_cfg = {"server": self.proxy_server}
                    if self.proxy_username:
                        proxy_cfg["username"] = self.proxy_username
                    if self.proxy_password:
                        proxy_cfg["password"] = self.proxy_password
                    launch_kwargs["proxy"] = proxy_cfg
                context = p.chromium.launch_persistent_context(**launch_kwargs)
                page = context.new_page()
                try:
                    page.goto(
                        self.creator_url,
                        wait_until="domcontentloaded",
                        timeout=self.wait_timeout_ms,
                    )
                except Exception as exc:
                    context.close()
                    return XHSPublishResult(
                        success=False,
                        error=(
                            f"访问小红书创作中心超时/失败: {exc}。"
                            f"请先在当前环境验证可访问 {self.creator_url}。"
                        ),
                    )

                if self._is_login_required(page):
                    shot = self._save_debug_screenshot(page, "login_required")
                    context.close()
                    return XHSPublishResult(
                        success=False,
                        error=(
                            "未检测到小红书登录态。请先运行 `python scripts/xhs_login.py` 完成登录。"
                            + (f" 调试截图: {shot}" if shot else "")
                        ),
                    )

                if not self._ensure_publish_editor(page, existing_paths, target_kind):
                    shot = self._save_debug_screenshot(page, "publish_entry_not_found")
                    current_url = page.url
                    context.close()
                    return XHSPublishResult(
                        success=False,
                        error=(
                            f"未进入发布编辑页，当前URL: {current_url}。"
                            "已尝试自动切换并等待手动选择30秒。请确认在页面中点击了对应的“图文/视频”入口。"
                            + (f" 调试截图: {shot}" if shot else "")
                        ),
                    )

                best_input = self._find_best_file_input(page, existing_paths, target_kind)
                if best_input is None:
                    shot = self._save_debug_screenshot(page, "file_input_missing")
                    context.close()
                    return XHSPublishResult(
                        success=False,
                        error=(
                            "进入发布编辑页后仍未找到上传框。"
                            + (f" 调试截图: {shot}" if shot else "")
                        ),
                    )
                file_input = best_input["locator"]
                accept = best_input["accept"]
                multiple = bool(best_input["multiple"])
                if not best_input.get("kind_matched", False):
                    shot = self._save_debug_screenshot(page, "input_kind_mismatch")
                    context.close()
                    return XHSPublishResult(
                        success=False,
                        error=(
                            f"当前页面上传框类型与素材不匹配，target_kind={target_kind}。"
                            f"accept={accept or '<empty>'}, multiple={multiple}, score={best_input.get('score')}。"
                            + (f" 调试截图: {shot}" if shot else "")
                        ),
                    )

                try:
                    if multiple or len(existing_paths) == 1:
                        file_input.set_input_files(existing_paths)
                    else:
                        # 非 multiple 上传框时，逐个尝试上传
                        for media_path in existing_paths:
                            file_input.set_input_files(media_path)
                            page.wait_for_timeout(350)
                except Exception as exc:
                    shot = self._save_debug_screenshot(page, "upload_failed")
                    context.close()
                    return XHSPublishResult(
                        success=False,
                        error=(
                            f"上传媒体失败: {exc}。"
                            f"上传框accept={accept or '<empty>'}, multiple={multiple}。"
                            + (f" 调试截图: {shot}" if shot else "")
                        ),
                    )
                if not self._wait_text_editor(page, 25000):
                    shot = self._save_debug_screenshot(page, "editor_not_ready")
                    current_url = page.url
                    context.close()
                    return XHSPublishResult(
                        success=False,
                        error=(
                            f"上传完成后未进入可编辑状态，当前URL: {current_url}。"
                            "请确认页面已完成素材处理，或稍后重试。"
                            + (f" 调试截图: {shot}" if shot else "")
                        ),
                    )
                try:
                    self._fill_title_and_body(page, title, body)
                except Exception as exc:
                    shot = self._save_debug_screenshot(page, "fill_text_failed")
                    context.close()
                    return XHSPublishResult(
                        success=False,
                        error=(
                            f"填写标题/正文失败: {exc}。"
                            + (f" 调试截图: {shot}" if shot else "")
                        ),
                    )
                page.wait_for_timeout(1200)
                filled_shot = self._save_debug_screenshot(page, "filled")

                if not self.auto_click_publish:
                    context.close()
                    return XHSPublishResult(
                        success=False,
                        error=(
                            "已填充内容但未点击发布。请将 XHS_AUTO_CLICK_PUBLISH=true 后重试。"
                            + (f" 调试截图: {filled_shot}" if filled_shot else "")
                        ),
                    )

                page.get_by_role("button", name=self.publish_button_text).first.click()
                page.wait_for_timeout(3000)

                current_url = page.url
                note_id = f"pw_{uuid4().hex[:12]}"
                if "/explore/" in current_url:
                    note_id = current_url.rsplit("/explore/", maxsplit=1)[-1].split("?")[0]
                context.close()

                return XHSPublishResult(
                    success=True,
                    note_id=note_id,
                    note_url=current_url,
                )
        except Exception as exc:  # pragma: no cover
            return XHSPublishResult(success=False, error=str(exc))


def check_xhs_login_data(user_data_dir: str) -> dict:
    """Check local login state snapshot."""
    root = Path(user_data_dir).resolve()
    if not root.exists():
        return {"ok": False, "message": f"浏览器数据目录不存在: {root}"}

    cookie_candidates = list(root.glob("**/Cookies"))
    local_storage_candidates = list(root.glob("**/Local Storage/**"))

    has_cookie = any(path.is_file() and path.stat().st_size > 0 for path in cookie_candidates)
    has_local_storage = any(path.exists() for path in local_storage_candidates)
    if has_cookie or has_local_storage:
        return {"ok": True, "message": "检测到本地登录数据（Cookies/Storage）"}
    return {"ok": False, "message": "未检测到登录数据，请先运行 scripts/xhs_login.py"}
