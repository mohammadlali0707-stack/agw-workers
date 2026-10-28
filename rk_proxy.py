#!/usr/bin/env python3
"""rk_proxy.py - Reply Keyboard Proxy and Inline Action Button Enforcement for LeadDesk bot."""
from __future__ import annotations
import copy, json, logging
from typing import Any, Callable
import urllib.error, urllib.request

logger = logging.getLogger("rk_proxy")

LANGUAGE_SWITCH_LABELS = {
    "فارسی", "انگلیسی", "🇮🇷 FA", "🇬🇧 EN", "🇮🇷 فارسی", "🇬🇧 انگلیسی", "FA", "EN",
}
TASK_FLEET_LABELS = {"کار برای فلش", "کار برای فلش به ناوگان", "آرگون به ناوگان"}
MAIN_MENU_ONLY_BUTTONS = LANGUAGE_SWITCH_LABELS | TASK_FLEET_LABELS

SUBMENU_BACK_BUTTONS = {
    "بازگشت", "🔙", "صفحه اصلی", "صفحهٔ اصلی", "منوی اصلی", "🏠",
    "Back", "Main Menu", "لغو", "انصراف", "Cancel",
}
MAIN_MENU_SIGNATURE_BUTTONS = {
    "کار برای فلش", "کار برای فلش به ناوگان", "آرگون به ناوگان",
    "وضعیت کلاستر", "گزارش روزانه", "پروژه‌ها", "تنظیمات", "راهنما",
    "وضعیت", "خدمات", "کلاستر", "Home", "Status", "Projects",
}
TASK_FLEET_BUTTONS = ["کار برای فلش", "کار برای فلش به ناوگان", "آرگون به ناوگان"]
STANDARD_INLINE_ACTIONS = [
    {"text": "🇮🇷 FA", "callback_data": "bilingual_fa:1"},
    {"text": "🇬🇧 EN", "callback_data": "bilingual_en:1"},
    {"text": "📘 Words", "callback_data": "bilingual_words:1"},
    {"text": "Knowledge", "callback_data": "bilingual_knowledge:1"},
]

def get_button_text(btn: Any) -> str:
    if isinstance(btn, dict):
        return str(btn.get("text", "")).strip()
    if hasattr(btn, "text"):
        return str(getattr(btn, "text", "")).strip()
    if isinstance(btn, str):
        return btn.strip()
    return str(btn).strip()

def is_main_menu_only_button(btn: Any) -> bool:
    text = get_button_text(btn)
    if not text:
        return False
    if text in MAIN_MENU_ONLY_BUTTONS or text.upper() in {"FA", "EN"}:
        return True
    if any(lbl == text or lbl in text for lbl in TASK_FLEET_LABELS):
        return True
    return any(lbl in text for lbl in ("🇮🇷 FA", "🇬🇧 EN", "🇮🇷 فارسی", "🇬🇧 انگلیسی"))

def is_back_or_return_button(btn: Any) -> bool:
    text = get_button_text(btn)
    return bool(text and any(back_lbl in text for back_lbl in SUBMENU_BACK_BUTTONS))

def is_main_menu_keyboard(
    keyboard_rows: Any,
    is_main_menu: bool | None = None,
    screen: str | None = None,
) -> bool:
    if is_main_menu is not None:
        return bool(is_main_menu)
    if screen is not None:
        s = screen.strip().lower()
        if s in ("home", "main", "main_menu", "صفحه اصلی", "صفحهٔ اصلی", "منوی اصلی"):
            return True
        if s in ("sub", "submenu", "settings", "secondary", "detail", "details"):
            return False
    rows = keyboard_rows
    if isinstance(keyboard_rows, dict):
        rows = keyboard_rows.get("keyboard", [])
    elif hasattr(keyboard_rows, "keyboard"):
        rows = getattr(keyboard_rows, "keyboard", [])
    if not rows:
        return False
    for row in rows:
        row_list = row if isinstance(row, list) else [row]
        for btn in row_list:
            if is_back_or_return_button(btn):
                return False
    for row in rows:
        row_list = row if isinstance(row, list) else [row]
        for btn in row_list:
            txt = get_button_text(btn)
            if any(sig in txt for sig in MAIN_MENU_SIGNATURE_BUTTONS):
                return True
    return False

def filter_submenu_reply_keyboard(keyboard_rows: Any) -> Any:
    is_dict = isinstance(keyboard_rows, dict)
    rows = keyboard_rows.get("keyboard", []) if is_dict else (keyboard_rows or [])
    filtered: list[list[Any]] = []
    for row in rows:
        row_list = row if isinstance(row, list) else [row]
        clean_row = [btn for btn in row_list if not is_main_menu_only_button(btn)]
        if clean_row:
            filtered.append(clean_row)
    if is_dict:
        res = copy.deepcopy(keyboard_rows)
        res["keyboard"] = filtered
        return res
    return filtered

def ensure_main_menu_reply_keyboard(keyboard_rows: Any) -> Any:
    is_dict = isinstance(keyboard_rows, dict)
    raw_rows = keyboard_rows.get("keyboard", []) if is_dict else (keyboard_rows or [])
    rows: list[list[Any]] = copy.deepcopy(raw_rows)
    is_btn_dict = any(isinstance(btn, dict) for r in rows for btn in (r if isinstance(r, list) else [r]))

    def _make_btn(text: str) -> Any:
        return {"text": text} if is_btn_dict else text

    existing_texts = {get_button_text(btn) for r in rows for btn in (r if isinstance(r, list) else [r])}
    missing_tasks = [t for t in TASK_FLEET_BUTTONS if not any(t in ext for ext in existing_texts)]
    if missing_tasks:
        rows.append([_make_btn(t) for t in missing_tasks])
        existing_texts.update(missing_tasks)

    has_fa = any("FA" in txt or "فارسی" in txt for txt in existing_texts)
    has_en = any("EN" in txt or "انگلیسی" in txt for txt in existing_texts)
    missing_langs: list[str] = []
    if not has_fa:
        missing_langs.append("🇮🇷 FA")
    if not has_en:
        missing_langs.append("🇬🇧 EN")
    if missing_langs:
        rows.append([_make_btn(l) for l in missing_langs])

    if is_dict:
        res = copy.deepcopy(keyboard_rows)
        res["keyboard"] = rows
        return res
    return rows

def process_reply_keyboard(
    keyboard_or_markup: Any,
    is_main_menu: bool | None = None,
    screen: str | None = None,
) -> Any:
    if isinstance(keyboard_or_markup, dict):
        res = copy.deepcopy(keyboard_or_markup)
        if "keyboard" in res:
            rows = res["keyboard"]
            if is_main_menu_keyboard(rows, is_main_menu=is_main_menu, screen=screen):
                res["keyboard"] = ensure_main_menu_reply_keyboard(rows)
            else:
                res["keyboard"] = filter_submenu_reply_keyboard(rows)
        return res
    if isinstance(keyboard_or_markup, list):
        rows = copy.deepcopy(keyboard_or_markup)
        if is_main_menu_keyboard(rows, is_main_menu=is_main_menu, screen=screen):
            return ensure_main_menu_reply_keyboard(rows)
        return filter_submenu_reply_keyboard(rows)
    return keyboard_or_markup

def build_inline_action_keyboard(
    existing_inline_keyboard: Any = None,
) -> list[list[dict[str, Any]]]:
    raw_rows = existing_inline_keyboard or []
    if isinstance(raw_rows, dict) and "inline_keyboard" in raw_rows:
        raw_rows = raw_rows["inline_keyboard"]
    rows: list[list[dict[str, Any]]] = []
    for r in raw_rows:
        row_list = r if isinstance(r, list) else [r]
        row_copy = []
        for btn in row_list:
            if isinstance(btn, dict):
                row_copy.append(dict(btn))
            elif hasattr(btn, "to_dict"):
                row_copy.append(btn.to_dict())
            elif hasattr(btn, "text"):
                d = {"text": getattr(btn, "text", "")}
                if hasattr(btn, "callback_data") and btn.callback_data:
                    d["callback_data"] = btn.callback_data
                row_copy.append(d)
            else:
                row_copy.append({"text": str(btn)})
        rows.append(row_copy)

    has_fa, has_en, has_words, has_knowledge = False, False, False, False

    for row in rows:
        for btn in row:
            txt = str(btn.get("text", "")).strip() if isinstance(btn, dict) else (getattr(btn, "text", "") or str(btn)).strip()
            cb = str(btn.get("callback_data", "")).strip() if isinstance(btn, dict) else (getattr(btn, "callback_data", "") or "")
            if "FA" in txt or "فارسی" in txt or "lang_fa" in cb or "bilingual_fa" in cb:
                has_fa = True
            if "EN" in txt or "انگلیسی" in txt or "lang_en" in cb or "bilingual_en" in cb:
                has_en = True
            if "Words" in txt or "words" in cb:
                has_words = True
            if "Knowledge" in txt or "knowledge" in cb:
                has_knowledge = True

    if has_fa and has_en and has_words and has_knowledge:
        return rows

    missing: list[dict[str, str]] = []
    if not has_fa:
        missing.append({"text": "🇮🇷 FA", "callback_data": "bilingual_fa:1"})
    if not has_en:
        missing.append({"text": "🇬🇧 EN", "callback_data": "bilingual_en:1"})
    if not has_words:
        missing.append({"text": "📘 Words", "callback_data": "bilingual_words:1"})
    if not has_knowledge:
        missing.append({"text": "Knowledge", "callback_data": "bilingual_knowledge:1"})

    if missing:
        rows.append(missing)
    return rows

def enforce_inline_action_buttons(reply_markup: dict[str, Any] | None = None) -> dict[str, Any]:
    markup = copy.deepcopy(reply_markup or {})
    markup["inline_keyboard"] = build_inline_action_keyboard(markup.get("inline_keyboard", []))
    return markup

def process_telegram_payload(
    payload: dict[str, Any],
    is_main_menu: bool | None = None,
    screen: str | None = None,
) -> dict[str, Any]:
    res = copy.deepcopy(payload)
    markup = res.get("reply_markup")

    if markup is None:
        inline_kb = build_inline_action_keyboard([])
        res["reply_markup"] = {"inline_keyboard": inline_kb}
        res["inline_keyboard"] = inline_kb
        return res

    if isinstance(markup, dict):
        if "keyboard" in markup:
            rows = markup["keyboard"]
            if is_main_menu_keyboard(rows, is_main_menu=is_main_menu, screen=screen):
                markup["keyboard"] = ensure_main_menu_reply_keyboard(rows)
            else:
                markup["keyboard"] = filter_submenu_reply_keyboard(rows)
            inline_kb = build_inline_action_keyboard(markup.get("inline_keyboard", []))
            markup["inline_keyboard"] = inline_kb
            res["reply_markup"] = markup
            res["reply_keyboard"] = markup["keyboard"]
            res["inline_keyboard"] = inline_kb
            return res

        if "inline_keyboard" in markup:
            inline_kb = build_inline_action_keyboard(markup["inline_keyboard"])
            markup["inline_keyboard"] = inline_kb
            res["reply_markup"] = markup
            res["inline_keyboard"] = inline_kb
            return res

        inline_kb = build_inline_action_keyboard(markup.get("inline_keyboard", []))
        markup["inline_keyboard"] = inline_kb
        res["reply_markup"] = markup
        res["inline_keyboard"] = inline_kb
        return res

    return res

def split_payload_for_telegram(
    payload: dict[str, Any],
    is_main_menu: bool | None = None,
    screen: str | None = None,
) -> list[dict[str, Any]]:
    processed = process_telegram_payload(payload, is_main_menu=is_main_menu, screen=screen)
    markup = processed.get("reply_markup", {})

    if isinstance(markup, dict) and "keyboard" in markup and "inline_keyboard" in markup:
        msg_payload = copy.deepcopy(processed)
        msg_payload["reply_markup"] = {"inline_keyboard": markup["inline_keyboard"]}
        rk_payload = {
            "chat_id": processed.get("chat_id"),
            "text": "🧭 منو به‌روز شد" if is_main_menu_keyboard(markup["keyboard"], is_main_menu, screen) else "⚙️",
            "reply_markup": {
                "keyboard": markup["keyboard"],
                "resize_keyboard": markup.get("resize_keyboard", True),
                "one_time_keyboard": markup.get("one_time_keyboard", False),
            },
        }
        return [msg_payload, rk_payload]
    return [processed]

class TelegramReplyKeyboardProxy:
    """Proxy handler and middleware for Telegram bot outgoing messages and keyboards."""

    def __init__(self, bot_token: str | None = None, base_url: str | None = None) -> None:
        self.bot_token = bot_token
        self.base_url = (base_url or "https://api.telegram.org").rstrip("/")

    is_main_menu_keyboard = staticmethod(is_main_menu_keyboard)
    filter_submenu_reply_keyboard = staticmethod(filter_submenu_reply_keyboard)
    ensure_main_menu_reply_keyboard = staticmethod(ensure_main_menu_reply_keyboard)
    process_reply_keyboard = staticmethod(process_reply_keyboard)
    build_inline_action_keyboard = staticmethod(build_inline_action_keyboard)
    enforce_inline_action_buttons = staticmethod(enforce_inline_action_buttons)
    intercept_payload = staticmethod(process_telegram_payload)
    split_payload_for_telegram = staticmethod(split_payload_for_telegram)

    def send_message(
        self,
        chat_id: int | str,
        text: str,
        reply_markup: dict[str, Any] | None = None,
        is_main_menu: bool | None = None,
        screen: str | None = None,
        sender_fn: Callable[[dict[str, Any]], Any] | None = None,
        **extra: Any,
    ) -> list[Any]:
        payload: dict[str, Any] = {"chat_id": chat_id, "text": text, **extra}
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup
        payloads = self.split_payload_for_telegram(payload, is_main_menu=is_main_menu, screen=screen)
        if sender_fn is not None:
            return [sender_fn(p) for p in payloads]
        results = []
        if self.bot_token:
            for p in payloads:
                url = f"{self.base_url}/bot{self.bot_token}/sendMessage"
                req = urllib.request.Request(
                    url,
                    data=json.dumps(p).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                )
                try:
                    with urllib.request.urlopen(req, timeout=10) as resp:
                        results.append(json.loads(resp.read().decode("utf-8")))
                except Exception as err:
                    logger.error("Failed to send message via proxy: %s", err)
                    results.append({"ok": False, "error": str(err)})
        else:
            results = payloads
        return results
