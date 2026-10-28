"""hq_service.py.

Telegram Bot HQ Service & Menu Keyboard Dispatcher for agw-workers.
Coordinates bottom ReplyKeyboardMarkup and per-message InlineKeyboardMarkup layouts:
1. Language switch buttons ('فارسی' / 'انگلیسی' / '🇮🇷 FA' / '🇬🇧 EN') and task/fleet
   buttons ('کار برای فلش', 'کار برای فلش به ناوگان', 'آرگون به ناوگان') appear ONLY on
   the main home menu screen ('صفحهٔ اصلی') in the bottom reply keyboard.
2. Every outgoing bot message consistently receives the four inline action buttons:
   Persian ('🇮🇷 FA'), English ('🇬🇧 EN'), Words ('📘 Words'), and Knowledge ('Knowledge').
3. Sub-menus and secondary screens strictly omit/strip any main-menu-only buttons.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from typing import Any, Callable

logger = logging.getLogger("hq_service")

# -----------------------------------------------------------------------------
# Shared Constants & Button Text Specifications
# -----------------------------------------------------------------------------

# Language switch buttons
LANGUAGE_SWITCH_BUTTONS: frozenset[str] = frozenset({
    "فارسی",
    "انگلیسی",
    "🇮🇷 FA",
    "🇬🇧 EN",
    "🇮🇷 فارسی",
    "🇬🇧 انگلیسی",
    "FA",
    "EN",
})

# Task / Fleet buttons for main menu
TASK_FLEET_BUTTONS: list[str] = [
    "کار برای فلش",
    "کار برای فلش به ناوگان",
    "آرگون به ناوگان",
]
TASK_FLEET_BUTTON_LABELS: frozenset[str] = frozenset(TASK_FLEET_BUTTONS)

# Combined main-menu-only buttons
MAIN_MENU_ONLY_BUTTONS: frozenset[str] = frozenset(
    LANGUAGE_SWITCH_BUTTONS | TASK_FLEET_BUTTON_LABELS
)

# Back / Return navigation labels indicating a sub-menu / secondary screen
BACK_NAVIGATION_LABELS: frozenset[str] = frozenset({
    "بازگشت",
    "🔙",
    "🔙 بازگشت",
    "🔙 بازگشت به منوی اصلی",
    "منوی اصلی",
    "🏠 منوی اصلی",
    "Back",
    "Main Menu",
    "🔙 Back",
    "لغو",
    "انصراف",
    "Cancel",
})

# Core main-menu signature button labels (top-level actions)
MAIN_MENU_SIGNATURES: frozenset[str] = frozenset({
    "مدیریت وظایف",
    "وظایف",
    "وضعیت ناوگان",
    "ناوگان",
    "تنظیمات",
    "راهنما",
    "📋 مدیریت وظایف",
    "📊 وضعیت ناوگان",
    "⚙️ تنظیمات",
    "ℹ️ راهنما",
    "Tasks",
    "Fleet Status",
    "Settings",
    "Help",
})


# -----------------------------------------------------------------------------
# Inspection & Classification Helpers
# -----------------------------------------------------------------------------

def extract_button_text(button: Any) -> str:
    """Safely extract plain text from a button object (str or dict)."""
    if isinstance(button, str):
        return button.strip()
    if isinstance(button, dict) and "text" in button:
        return str(button["text"]).strip()
    return ""


def is_back_or_return_button(button_text: str) -> bool:
    """Check if button text indicates navigation back to the main menu or cancellation."""
    if not button_text:
        return False
    clean = button_text.strip()
    if clean in BACK_NAVIGATION_LABELS:
        return True
    for label in BACK_NAVIGATION_LABELS:
        if clean.startswith(label) or clean.endswith(label):
            return True
    return False


def is_main_menu_only_button(button_text: str) -> bool:
    """Check if button text strictly matches any main-menu-only button."""
    if not button_text:
        return False
    clean = button_text.strip()
    return clean in MAIN_MENU_ONLY_BUTTONS


def is_main_menu_keyboard(keyboard_or_rows: Any, is_main_menu: bool | None = None) -> bool:
    """
    Detect whether a ReplyKeyboardMarkup or keyboard rows list represents the Main Home Menu.

    Rules from SDD:
    A ReplyKeyboardMarkup represents a sub-menu / non-home screen if:
      1. Explicit screen context/flag indicates a sub-menu (is_main_menu=False), OR
      2. Any button contains a back/return navigation label, OR
      3. The keyboard does not contain the core top-level main-menu navigation signature.
    """
    if is_main_menu is not None:
        return bool(is_main_menu)

    rows = keyboard_or_rows
    if isinstance(keyboard_or_rows, dict):
        rows = keyboard_or_rows.get("keyboard", [])

    if not isinstance(rows, list) or not rows:
        return False

    button_texts: list[str] = []
    for row in rows:
        row_list = row if isinstance(row, list) else [row]
        for btn in row_list:
            txt = extract_button_text(btn)
            if txt:
                button_texts.append(txt)

    if not button_texts:
        return False

    # Rule 2: If any button contains a back/return label, it is a sub-menu
    for txt in button_texts:
        if is_back_or_return_button(txt):
            return False

    # Rule 3: Check for main-menu signature or task/fleet buttons
    has_fleet = any(btn in button_texts for btn in TASK_FLEET_BUTTONS)
    has_main_sig = any(
        any(sig in txt for sig in MAIN_MENU_SIGNATURES)
        for txt in button_texts
    )

    return has_fleet or has_main_sig


# -----------------------------------------------------------------------------
# Reply Keyboard Sanitization & Building
# -----------------------------------------------------------------------------

def strip_main_menu_buttons_from_rows(rows: list[Any]) -> list[list[Any]]:
    """
    Strip any buttons matching MAIN_MENU_ONLY_BUTTONS from keyboard rows.
    Guarantees valid 2D array structure list[list[Any]].
    """
    cleaned_rows: list[list[Any]] = []
    for row in rows:
        row_list = row if isinstance(row, list) else [row]
        new_row: list[Any] = []
        for btn in row_list:
            txt = extract_button_text(btn)
            if not is_main_menu_only_button(txt):
                new_row.append(btn)
        if new_row:
            cleaned_rows.append(new_row)
    return cleaned_rows


def ensure_main_menu_buttons(reply_markup: dict[str, Any]) -> dict[str, Any]:
    """Ensure that the main home menu keyboard contains task/fleet and language buttons."""
    rows = [list(r) if isinstance(r, list) else [r] for r in reply_markup.get("keyboard", [])]

    # Check for fleet buttons
    has_fleet = False
    for row in rows:
        for btn in row:
            txt = extract_button_text(btn)
            if txt in TASK_FLEET_BUTTON_LABELS:
                has_fleet = True
                break
        if has_fleet:
            break

    if not has_fleet:
        rows.append(list(TASK_FLEET_BUTTONS))

    # Check for language switch buttons
    has_lang = False
    for row in rows:
        for btn in row:
            txt = extract_button_text(btn)
            if txt in LANGUAGE_SWITCH_BUTTONS:
                has_lang = True
                break
        if has_lang:
            break

    if not has_lang:
        rows.append(["🇮🇷 FA", "🇬🇧 EN"])

    res = dict(reply_markup)
    res["keyboard"] = rows
    return res


def filter_reply_keyboard(reply_markup: dict[str, Any] | None, is_main_menu: bool | None = None) -> dict[str, Any] | None:
    """
    Filter or sanitize a ReplyKeyboardMarkup dict:
    - On Sub-Menus: completely strips all MAIN_MENU_ONLY_BUTTONS.
    - On Main Menu: ensures task/fleet buttons and language switch buttons are present.
    """
    if not reply_markup or "keyboard" not in reply_markup:
        return reply_markup

    is_main = is_main_menu_keyboard(reply_markup, is_main_menu=is_main_menu)
    rows = reply_markup.get("keyboard", [])

    if not is_main:
        cleaned = strip_main_menu_buttons_from_rows(rows)
        res = dict(reply_markup)
        res["keyboard"] = cleaned
        return res
    else:
        return ensure_main_menu_buttons(reply_markup)


def build_main_menu_keyboard(lang: str = "fa") -> dict[str, Any]:
    """
    Build ReplyKeyboardMarkup for the Main Home Menu ('صفحهٔ اصلی').
    Includes:
    - Primary services / navigation rows
    - Fleet / task buttons: ['کار برای فلش', 'کار برای فلش به ناوگان', 'آرگون به ناوگان']
    - Bottom language switch buttons: ['🇮🇷 FA', '🇬🇧 EN']
    """
    if lang == "en":
        keyboard = [
            ["📋 Tasks", "📊 Fleet Status"],
            ["⚙️ Settings", "ℹ️ Help"],
            list(TASK_FLEET_BUTTONS),
            ["🇮🇷 FA", "🇬🇧 EN"],
        ]
    else:
        keyboard = [
            ["📋 مدیریت وظایف", "📊 وضعیت ناوگان"],
            ["⚙️ تنظیمات", "ℹ️ راهنما"],
            list(TASK_FLEET_BUTTONS),
            ["🇮🇷 FA", "🇬🇧 EN"],
        ]

    return {
        "keyboard": keyboard,
        "resize_keyboard": True,
        "one_time_keyboard": False,
        "is_persistent": True,
    }


def build_tasks_menu_keyboard(lang: str = "fa") -> dict[str, Any]:
    """Build Sub-Menu ReplyKeyboardMarkup for Task Management (no main-menu-only buttons)."""
    if lang == "en":
        keyboard = [
            ["➕ Create Task", "📌 Active Tasks"],
            ["✅ Completed Tasks", "🔍 Search Tasks"],
            ["🔙 Main Menu"],
        ]
    else:
        keyboard = [
            ["➕ ایجاد وظبیه جدید", "📌 وظایف فعال"],
            ["✅ وظایف تکمیل‌شده", "🔍 جستجوی وظیفه"],
            ["🔙 بازگشت به منوی اصلی"],
        ]
    markup = {
        "keyboard": keyboard,
        "resize_keyboard": True,
        "one_time_keyboard": False,
    }
    return filter_reply_keyboard(markup, is_main_menu=False) or markup


def build_fleet_menu_keyboard(lang: str = "fa") -> dict[str, Any]:
    """Build Sub-Menu ReplyKeyboardMarkup for Fleet Status (no main-menu-only buttons)."""
    if lang == "en":
        keyboard = [
            ["⚡ Flash Workers Status", "🤖 Argon Status"],
            ["🔄 Refresh Fleet State"],
            ["🔙 Main Menu"],
        ]
    else:
        keyboard = [
            ["⚡ وضعیت کارگران فلش", "🤖 وضعیت آرگون"],
            ["🔄 بازخوانی وضعیت ناوگان"],
            ["🔙 بازگشت به منوی اصلی"],
        ]
    markup = {
        "keyboard": keyboard,
        "resize_keyboard": True,
        "one_time_keyboard": False,
    }
    return filter_reply_keyboard(markup, is_main_menu=False) or markup


def build_settings_menu_keyboard(lang: str = "fa") -> dict[str, Any]:
    """Build Sub-Menu ReplyKeyboardMarkup for Settings (no main-menu-only buttons)."""
    if lang == "en":
        keyboard = [
            ["🔔 Notifications", "🔑 Access Keys"],
            ["🔙 Main Menu"],
        ]
    else:
        keyboard = [
            ["🔔 اعلان‌ها", "🔑 کلیدهای دسترسی"],
            ["🔙 بازگشت به منوی اصلی"],
        ]
    markup = {
        "keyboard": keyboard,
        "resize_keyboard": True,
        "one_time_keyboard": False,
    }
    return filter_reply_keyboard(markup, is_main_menu=False) or markup


def build_help_menu_keyboard(lang: str = "fa") -> dict[str, Any]:
    """Build Sub-Menu ReplyKeyboardMarkup for Help (no main-menu-only buttons)."""
    if lang == "en":
        keyboard = [
            ["📖 Documentation", "💬 Support"],
            ["🔙 Main Menu"],
        ]
    else:
        keyboard = [
            ["📖 مستندات", "💬 پشتیبانی"],
            ["🔙 بازگشت به منوی اصلی"],
        ]
    markup = {
        "keyboard": keyboard,
        "resize_keyboard": True,
        "one_time_keyboard": False,
    }
    return filter_reply_keyboard(markup, is_main_menu=False) or markup


def build_sub_menu_keyboard(
    items: list[list[str]],
    back_label: str = "🔙 بازگشت به منوی اصلی",
    is_main_menu: bool = False,
) -> dict[str, Any]:
    """
    Generic Sub-Menu ReplyKeyboardMarkup builder.
    Strips any language switch or task/fleet buttons if inadvertently provided.
    """
    keyboard = [list(row) for row in items]
    if back_label and not any(is_back_or_return_button(extract_button_text(b)) for row in keyboard for b in row):
        keyboard.append([back_label])

    markup = {
        "keyboard": keyboard,
        "resize_keyboard": True,
        "one_time_keyboard": False,
    }
    return filter_reply_keyboard(markup, is_main_menu=False) or markup


# -----------------------------------------------------------------------------
# Inline Action Buttons Construction & Enforcement
# -----------------------------------------------------------------------------

def get_standard_inline_action_row(message_id: int | str | None = None) -> list[dict[str, str]]:
    """Return the 4 standard inline action buttons row with optional message_id suffix."""
    suffix = f":{message_id}" if message_id is not None else ""
    return [
        {"text": "🇮🇷 FA", "callback_data": f"bilingual_fa{suffix}"},
        {"text": "🇬🇧 EN", "callback_data": f"bilingual_en{suffix}"},
        {"text": "📘 Words", "callback_data": f"bilingual_words{suffix}"},
        {"text": "Knowledge", "callback_data": f"bilingual_knowledge{suffix}"},
    ]


def has_all_four_action_buttons(inline_keyboard: list[list[dict[str, Any]]]) -> bool:
    """Verify whether all 4 required inline action buttons are present."""
    found: set[str] = set()
    for row in inline_keyboard:
        for btn in row:
            txt = str(btn.get("text", "")).strip()
            cb = str(btn.get("callback_data", "")).split(":")[0].strip()

            # Persian check
            if "FA" in txt or "فارسی" in txt or cb in ("lang_fa", "bilingual_fa", "fa"):
                found.add("fa")
            # English check
            if "EN" in txt or "انگلیسی" in txt or cb in ("lang_en", "bilingual_en", "en"):
                found.add("en")
            # Words check
            if "Words" in txt or cb in ("words", "bilingual_words"):
                found.add("words")
            # Knowledge check
            if "Knowledge" in txt or cb in ("knowledge", "bilingual_knowledge"):
                found.add("knowledge")

    return len(found) == 4


def build_inline_action_keyboard(
    custom_buttons: list[list[dict[str, Any]]] | None = None,
    message_id: int | str | None = None,
) -> dict[str, Any]:
    """
    Construct an InlineKeyboardMarkup containing any message-specific custom buttons,
    followed by the 4 standard action buttons: Persian, English, Words, Knowledge.
    """
    rows: list[list[dict[str, Any]]] = []
    if custom_buttons:
        for r in custom_buttons:
            rows.append([dict(b) for b in r])

    if not has_all_four_action_buttons(rows):
        rows.append(get_standard_inline_action_row(message_id=message_id))

    return {"inline_keyboard": rows}


def ensure_inline_action_buttons(
    reply_markup: dict[str, Any] | None = None,
    message_id: int | str | None = None,
) -> dict[str, Any]:
    """
    Ensure an outgoing bot message has the 4 inline action buttons attached:
    - If reply_markup is None: returns InlineKeyboardMarkup with the 4 action buttons.
    - If reply_markup is InlineKeyboardMarkup: appends 4 action buttons if missing.
    - If reply_markup is ReplyKeyboardMarkup: attaches 'inline_keyboard' dual representation.
    """
    if not reply_markup:
        return {"inline_keyboard": [get_standard_inline_action_row(message_id=message_id)]}

    if "inline_keyboard" in reply_markup:
        rows = [list(r) for r in reply_markup.get("inline_keyboard", [])]
        if not has_all_four_action_buttons(rows):
            rows.append(get_standard_inline_action_row(message_id=message_id))
        res = dict(reply_markup)
        res["inline_keyboard"] = rows
        return res

    # If it is ReplyKeyboardMarkup
    res = dict(reply_markup)
    if "inline_keyboard" not in res:
        res["inline_keyboard"] = [get_standard_inline_action_row(message_id=message_id)]
    return res


def attach_inline_action_buttons(
    message_payload: dict[str, Any],
    message_id: int | str | None = None,
) -> dict[str, Any]:
    """
    Ensure an outgoing message payload dict receives the 4 inline action buttons.
    If the payload already has reply_markup, merges or preserves it.
    """
    payload = dict(message_payload)
    current_markup = payload.get("reply_markup")
    if current_markup and "inline_keyboard" in current_markup:
        payload["reply_markup"] = ensure_inline_action_buttons(current_markup, message_id=message_id)
    elif current_markup and "keyboard" in current_markup:
        # ReplyKeyboardMarkup: keep it in reply_keyboard and set inline buttons
        payload["reply_keyboard"] = current_markup
        payload["reply_markup"] = build_inline_action_keyboard(message_id=message_id)
        payload["inline_keyboard"] = payload["reply_markup"]["inline_keyboard"]
    else:
        payload["reply_markup"] = build_inline_action_keyboard(message_id=message_id)
    return payload


# -----------------------------------------------------------------------------
# HQ Service Class & Message Dispatching
# -----------------------------------------------------------------------------

class HQService:
    """
    Central LeadDesk AI HQ Bot Service.
    Handles menu definitions, keyboard layout filtering, outgoing message dispatch,
    and callback/text routing.
    """

    def __init__(self, bot_token: str | None = None, default_lang: str = "fa"):
        self.bot_token = bot_token or os.environ.get("TELEGRAM_BOT_TOKEN", "")
        self.default_lang = default_lang
        self.user_languages: dict[int | str, str] = {}
        self.sent_messages: list[dict[str, Any]] = []

    def get_user_language(self, user_id: int | str) -> str:
        return self.user_languages.get(user_id, self.default_lang)

    def set_user_language(self, user_id: int | str, lang: str) -> None:
        if lang in ("fa", "en"):
            self.user_languages[user_id] = lang

    def prepare_message(
        self,
        chat_id: int | str,
        text: str,
        reply_markup: dict[str, Any] | None = None,
        is_main_menu: bool | None = None,
        parse_mode: str = "HTML",
        message_id: int | str | None = None,
    ) -> dict[str, Any]:
        """
        Prepare an outgoing message payload guaranteeing:
        1. Every outgoing bot message has the 4 inline action buttons.
        2. Any reply keyboard is strictly filtered according to main-menu vs. sub-menu rules.
        """
        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": parse_mode,
        }

        standard_inline = build_inline_action_keyboard(message_id=message_id)

        if reply_markup is None:
            payload["reply_markup"] = standard_inline
        elif "inline_keyboard" in reply_markup:
            payload["reply_markup"] = ensure_inline_action_buttons(reply_markup, message_id=message_id)
        elif "keyboard" in reply_markup:
            filtered_rk = filter_reply_keyboard(reply_markup, is_main_menu=is_main_menu)
            payload["reply_keyboard"] = filtered_rk
            payload["reply_markup"] = standard_inline
            payload["inline_keyboard"] = standard_inline["inline_keyboard"]
        else:
            payload["reply_markup"] = ensure_inline_action_buttons(reply_markup, message_id=message_id)

        return payload

    def send_message(
        self,
        chat_id: int | str,
        text: str,
        reply_markup: dict[str, Any] | None = None,
        is_main_menu: bool | None = None,
        parse_mode: str = "HTML",
        message_id: int | str | None = None,
    ) -> dict[str, Any]:
        """Dispatch a message with the 4 inline action buttons and filtered reply keyboard."""
        payload = self.prepare_message(
            chat_id=chat_id,
            text=text,
            reply_markup=reply_markup,
            is_main_menu=is_main_menu,
            parse_mode=parse_mode,
            message_id=message_id,
        )
        self.sent_messages.append(payload)
        return payload

    def edit_message_text(
        self,
        chat_id: int | str,
        message_id: int,
        text: str,
        reply_markup: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Edit an existing message, ensuring the 4 inline action buttons are preserved."""
        inline_markup = ensure_inline_action_buttons(reply_markup, message_id=message_id)
        payload = {
            "chat_id": chat_id,
            "message_id": message_id,
            "text": text,
            "reply_markup": inline_markup,
        }
        self.sent_messages.append(payload)
        return payload

    def handle_callback_query(self, callback_query: dict[str, Any]) -> dict[str, Any]:
        """
        Handle callback queries for all 4 inline action buttons:
        - Persian ('bilingual_fa:1' / 'lang_fa' / 'bilingual_fa')
        - English ('bilingual_en:1' / 'lang_en' / 'bilingual_en')
        - Words ('bilingual_words:1' / 'words' / 'bilingual_words')
        - Knowledge ('bilingual_knowledge:1' / 'knowledge' / 'bilingual_knowledge')
        """
        raw_data = str(callback_query.get("data", "")).strip()
        data = raw_data.split(":")[0].strip()
        chat_id = callback_query.get("message", {}).get("chat", {}).get("id") or callback_query.get("from", {}).get("id")
        user_id = callback_query.get("from", {}).get("id", chat_id)

        # 1. Persian switch
        if data in ("lang_fa", "bilingual_fa", "fa"):
            self.set_user_language(user_id, "fa")
            return {
                "action": "language_switched",
                "lang": "fa",
                "text": "🇮🇷 زبان به فارسی تغییر یافت.",
                "response": "زبان به فارسی تنظیم شد.",
            }

        # 2. English switch
        if data in ("lang_en", "bilingual_en", "en"):
            self.set_user_language(user_id, "en")
            return {
                "action": "language_switched",
                "lang": "en",
                "text": "🇬🇧 Language switched to English.",
                "response": "Language set to English.",
            }

        # 3. Words
        if data in ("words", "bilingual_words"):
            return {
                "action": "show_words",
                "title": "📘 Words / واژه‌نامه",
                "text": "📘 واژه‌نامه دو زبانه و اصطلاحات کلیدی کلاستر:\n- Fleet: ناوگان کارگران\n- Worker: کارگر مستقل\n- Queue: صف وظایف",
                "response": "📘 Words",
            }

        # 4. Knowledge
        if data in ("knowledge", "bilingual_knowledge"):
            return {
                "action": "show_knowledge",
                "title": "Knowledge / پایگاه دانش",
                "text": "🧠 پایگاه دانش کلاستر LeadDesk AI:\n- مستندات و معماری SDD\n- دفترچه تصمیمات فنی\n- وضعیت تسک‌ها و هماهنگی ایجنت‌ها",
                "response": "Knowledge",
            }

        return {"action": "unknown", "data": raw_data}

    def handle_text_message(self, message: dict[str, Any] | str, user_id: int | str = 0) -> dict[str, Any]:
        """
        Handle inbound text messages, navigating menus and handling main-menu-only buttons.
        """
        if isinstance(message, dict):
            text = (message.get("text") or "").strip()
            user_id = message.get("from", {}).get("id") or message.get("chat", {}).get("id") or user_id
        else:
            text = str(message).strip()

        lang = self.get_user_language(user_id)

        # 1. Main menu navigation & Return buttons
        if text in (
            "صفحه اصلی", "صفحهٔ اصلی", "منوی اصلی", "🏠", "Back", "Main Menu",
            "بازگشت", "🔙", "🔙 بازگشت", "🔙 بازگشت به منوی اصلی"
        ):
            rk = build_main_menu_keyboard(lang=lang)
            welcome_text = "🏠 صفحهٔ اصلی کلاستر LeadDesk AI:" if lang == "fa" else "🏠 LeadDesk AI Main Menu:"
            return self.send_message(chat_id=user_id, text=welcome_text, reply_markup=rk, is_main_menu=True)

        # 2. Sub-menu triggers
        if text in ("📋 مدیریت وظایف", "📋 Tasks", "مدیریت وظایف", "وظایف", "Tasks"):
            rk = build_tasks_menu_keyboard(lang=lang)
            txt = "📋 منوی مدیریت وظایف:\nلطفاً گزینه مورد نظر را انتخاب کنید." if lang == "fa" else "📋 Task Management Menu:\nPlease select an option."
            return self.send_message(chat_id=user_id, text=txt, reply_markup=rk, is_main_menu=False)

        if text in ("📊 وضعیت ناوگان", "📊 Fleet Status", "وضعیت ناوگان", "ناوگان", "Fleet"):
            rk = build_fleet_menu_keyboard(lang=lang)
            txt = "📊 وضعیت ناوگان ایجنت‌ها و کارگران فعال کلاستر:" if lang == "fa" else "📊 Fleet & Active Worker Status:"
            return self.send_message(chat_id=user_id, text=txt, reply_markup=rk, is_main_menu=False)

        if text in ("⚙️ تنظیمات", "⚙️ Settings", "تنظیمات", "Settings"):
            rk = build_settings_menu_keyboard(lang=lang)
            txt = "⚙️ تنظیمات سیستم و اعلان‌ها:" if lang == "fa" else "⚙️ System & Notification Settings:"
            return self.send_message(chat_id=user_id, text=txt, reply_markup=rk, is_main_menu=False)

        if text in ("ℹ️ راهنما", "ℹ️ Help", "راهنما", "Help"):
            rk = build_help_menu_keyboard(lang=lang)
            txt = "ℹ️ راهنمای کاربری ربات کلاستر:" if lang == "fa" else "ℹ️ Bot User Guide & Help:"
            return self.send_message(chat_id=user_id, text=txt, reply_markup=rk, is_main_menu=False)

        # 3. Task / Fleet buttons (Main-menu actions)
        if text == "کار برای فلش":
            txt = "⚡ ایجاد وظیفه برای کارگر فلش (Gemini Flash Worker)..."
            return self.send_message(chat_id=user_id, text=txt)

        if text == "کار برای فلش به ناوگان":
            txt = "🚀 پخش وظیفه فلش به کل ناوگان کارگران..."
            return self.send_message(chat_id=user_id, text=txt)

        if text == "آرگون به ناوگان":
            txt = "🤖 ارسال دستور پلنر آرگون به ناوگان اجرایی..."
            return self.send_message(chat_id=user_id, text=txt)

        # 4. Language switch buttons
        if text in ("🇮🇷 FA", "فارسی", "🇮🇷 فارسی", "FA"):
            self.set_user_language(user_id, "fa")
            rk = build_main_menu_keyboard(lang="fa")
            return self.send_message(chat_id=user_id, text="🇮🇷 زبان رابط کاربری به فارسی تنظیم شد.", reply_markup=rk, is_main_menu=True)

        if text in ("🇬🇧 EN", "انگلیسی", "🇬🇧 انگلیسی", "EN"):
            self.set_user_language(user_id, "en")
            rk = build_main_menu_keyboard(lang="en")
            return self.send_message(chat_id=user_id, text="🇬🇧 UI language switched to English.", reply_markup=rk, is_main_menu=True)

        # Default echo / acknowledgement
        default_resp = f"دستور دریافت شد: {text}" if lang == "fa" else f"Received command: {text}"
        return self.send_message(chat_id=user_id, text=default_resp)


# -----------------------------------------------------------------------------
# Default Singleton Instance & Module-Level Convenience Functions
# -----------------------------------------------------------------------------

_DEFAULT_SERVICE = HQService()

get_user_language = _DEFAULT_SERVICE.get_user_language
set_user_language = _DEFAULT_SERVICE.set_user_language
prepare_message = _DEFAULT_SERVICE.prepare_message
send_message = _DEFAULT_SERVICE.send_message
edit_message_text = _DEFAULT_SERVICE.edit_message_text
handle_callback_query = _DEFAULT_SERVICE.handle_callback_query
handle_text_message = _DEFAULT_SERVICE.handle_text_message

# Register under hq.hq_service as well for versatile import compatibility
if __name__ != "__main__":
    _this_module = sys.modules[__name__]
    sys.modules.setdefault("hq_service", _this_module)
    sys.modules.setdefault("hq.hq_service", _this_module)
