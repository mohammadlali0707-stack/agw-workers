"""bilingual.py - Telegram Bot Bilingual & Navigation Keyboard Helper.

Part of LeadDesk AI / HQ Service Telegram Bot layer.
Provides:
1. Per-message Inline Action Buttons (Persian, English, Words, Knowledge):
   - Ensures every message has all 4 inline action buttons idempotently without duplication.
   - Preserves message-specific custom inline buttons.
   - Appends only missing buttons if some are already present.
2. Bottom Reply Keyboard filtering:
   - Language switch buttons (فارسی / انگلیسی / 🇮🇷 FA / 🇬🇧 EN) and fleet/task buttons
     (کار برای فلش, کار برای فلش به ناوگان, آرگون به ناوگان) appear ONLY on the main home menu.
   - Omitted and stripped from all sub-menus and secondary screens (screens with return/back navigation).
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
import logging
from typing import Any

logger = logging.getLogger("bilingual")

# ==============================================================================
# Shared Constants (Aligned with SDD P10 contract)
# ==============================================================================

# Language switch buttons
LANGUAGE_SWITCH_BUTTONS: set[str] = {
    "فارسی",
    "انگلیسی",
    "🇮🇷 FA",
    "🇬🇧 EN",
    "🇮🇷 فارسی",
    "🇬🇧 انگلیسی",
    "FA",
    "EN",
}

# Task and fleet buttons
TASK_FLEET_BUTTONS: set[str] = {
    "کار برای فلش",
    "کار برای فلش به ناوگان",
    "آرگون به ناوگان",
}

# Main-Menu-Only Reply Keyboard Buttons
MAIN_MENU_ONLY_BUTTONS: set[str] = LANGUAGE_SWITCH_BUTTONS | TASK_FLEET_BUTTONS

# Sub-menu / Non-home navigation return labels
NAVIGATION_RETURN_LABELS: set[str] = {
    "بازگشت",
    "🔙",
    "صفحه اصلی",
    "صفحهٔ اصلی",
    "منوی اصلی",
    "🏠",
    "Back",
    "Main Menu",
    "لغو",
    "انصراف",
    "Cancel",
}

# Standard inline action button labels
LABEL_PERSIA: str = "🇮🇷 FA"
LABEL_ENGLISH: str = "🇬🇧 EN"
LABEL_WORDS: str = "📘 Words"
LABEL_KNOWLEDGE: str = "Knowledge"

# Standard inline action button callback_data
CALLBACK_PERSIA: str = "bilingual_fa:1"
CALLBACK_ENGLISH: str = "bilingual_en:1"
CALLBACK_WORDS: str = "bilingual_words:1"
CALLBACK_KNOWLEDGE: str = "bilingual_knowledge:1"

# Accepted callback_data prefixes / aliases for recognition
CALLBACK_PERSIA_ALIASES: tuple[str, ...] = ("bilingual_fa", "lang_fa")
CALLBACK_ENGLISH_ALIASES: tuple[str, ...] = ("bilingual_en", "lang_en")
CALLBACK_WORDS_ALIASES: tuple[str, ...] = ("bilingual_words", "words")
CALLBACK_KNOWLEDGE_ALIASES: tuple[str, ...] = ("bilingual_knowledge", "knowledge")


# ==============================================================================
# Telegram Keyboard Data Models (Dict & Object Compatible)
# ==============================================================================

@dataclass
class InlineKeyboardButton:
    text: str
    callback_data: str | None = None
    url: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"text": self.text}
        if self.callback_data is not None:
            d["callback_data"] = self.callback_data
        if self.url is not None:
            d["url"] = self.url
        return d


@dataclass
class InlineKeyboardMarkup:
    inline_keyboard: list[list[Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "inline_keyboard": [
                [
                    btn.to_dict() if isinstance(btn, InlineKeyboardButton)
                    else (btn if isinstance(btn, dict) else {"text": str(btn)})
                    for btn in row
                ]
                for row in self.inline_keyboard
            ]
        }


@dataclass
class KeyboardButton:
    text: str

    def to_dict(self) -> dict[str, Any]:
        return {"text": self.text}


@dataclass
class ReplyKeyboardMarkup:
    keyboard: list[list[Any]] = field(default_factory=list)
    resize_keyboard: bool = True
    one_time_keyboard: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "keyboard": [
                [
                    btn.to_dict() if isinstance(btn, KeyboardButton)
                    else (btn if isinstance(btn, dict) else {"text": str(btn)})
                    for btn in row
                ]
                for row in self.keyboard
            ],
            "resize_keyboard": self.resize_keyboard,
            "one_time_keyboard": self.one_time_keyboard,
        }


# ==============================================================================
# Helper / Inspection Utilities
# ==============================================================================

def extract_button_text(btn: Any) -> str:
    """Extract button label text whether button is str, dict, or object."""
    if btn is None:
        return ""
    if isinstance(btn, str):
        return btn.strip()
    if isinstance(btn, dict):
        return str(btn.get("text", "")).strip()
    if hasattr(btn, "text"):
        return str(getattr(btn, "text", "")).strip()
    return str(btn).strip()


def extract_button_callback_data(btn: Any) -> str:
    """Extract button callback_data whether button is dict or object."""
    if btn is None:
        return ""
    if isinstance(btn, dict):
        return str(btn.get("callback_data", "")).strip()
    if hasattr(btn, "callback_data"):
        return str(getattr(btn, "callback_data", "")).strip()
    return ""


def is_persian_button(btn: Any) -> bool:
    """Check if button is the Persian inline action button."""
    cb = extract_button_callback_data(btn)
    if any(cb == alias or cb.startswith(alias + ":") or cb.startswith(alias + "_") for alias in CALLBACK_PERSIA_ALIASES):
        return True
    txt = extract_button_text(btn)
    return txt in {"🇮🇷 FA", "فارسی", "🇮🇷 فارسی", "FA"}


def is_english_button(btn: Any) -> bool:
    """Check if button is the English inline action button."""
    cb = extract_button_callback_data(btn)
    if any(cb == alias or cb.startswith(alias + ":") or cb.startswith(alias + "_") for alias in CALLBACK_ENGLISH_ALIASES):
        return True
    txt = extract_button_text(btn)
    return txt in {"🇬🇧 EN", "انگلیسی", "🇬🇧 انگلیسی", "EN"}


def is_words_button(btn: Any) -> bool:
    """Check if button is the Words inline action button."""
    cb = extract_button_callback_data(btn)
    if any(cb == alias or cb.startswith(alias + ":") or cb.startswith(alias + "_") for alias in CALLBACK_WORDS_ALIASES):
        return True
    txt = extract_button_text(btn)
    return txt in {"📘 Words", "Words"}


def is_knowledge_button(btn: Any) -> bool:
    """Check if button is the Knowledge inline action button."""
    cb = extract_button_callback_data(btn)
    if any(cb == alias or cb.startswith(alias + ":") or cb.startswith(alias + "_") for alias in CALLBACK_KNOWLEDGE_ALIASES):
        return True
    txt = extract_button_text(btn)
    return txt in {"Knowledge", "🧠 Knowledge"}


def is_main_menu_only_button(btn: Any) -> bool:
    """Check if button text matches any main-menu-only button label."""
    txt = extract_button_text(btn)
    return txt in MAIN_MENU_ONLY_BUTTONS


def is_return_navigation_button(btn: Any) -> bool:
    """Check if button represents back/return navigation."""
    txt = extract_button_text(btn)
    if not txt:
        return False
    if txt in NAVIGATION_RETURN_LABELS:
        return True
    for nav in NAVIGATION_RETURN_LABELS:
        if nav in txt:
            return True
    return False


def is_main_menu_keyboard(
    keyboard_or_rows: Any,
    is_main_menu: bool | None = None,
) -> bool:
    """Detect whether a reply keyboard represents the main home menu vs a sub-menu.

    Rules:
    1. If `is_main_menu` is explicitly False, it is a sub-menu.
    2. If `is_main_menu` is explicitly True, it is main menu.
    3. If any button contains a return navigation label (e.g., 'بازگشت', '🔙', 'Back'),
       it is a sub-menu (returns False).
    4. Otherwise, defaults to True (main menu).
    """
    if is_main_menu is False:
        return False
    if is_main_menu is True:
        return True

    rows = _extract_keyboard_rows(keyboard_or_rows)
    for row in rows:
        for btn in row:
            if is_return_navigation_button(btn):
                return False
    return True


def _extract_keyboard_rows(keyboard_or_rows: Any) -> list[list[Any]]:
    """Extract 2D list of buttons from various keyboard representations."""
    if keyboard_or_rows is None:
        return []
    if isinstance(keyboard_or_rows, dict):
        if "keyboard" in keyboard_or_rows:
            return keyboard_or_rows.get("keyboard") or []
        if "inline_keyboard" in keyboard_or_rows:
            return keyboard_or_rows.get("inline_keyboard") or []
        return []
    if hasattr(keyboard_or_rows, "keyboard"):
        return getattr(keyboard_or_rows, "keyboard") or []
    if hasattr(keyboard_or_rows, "inline_keyboard"):
        return getattr(keyboard_or_rows, "inline_keyboard") or []
    if isinstance(keyboard_or_rows, list):
        if not keyboard_or_rows:
            return []
        if isinstance(keyboard_or_rows[0], list):
            return keyboard_or_rows
        return [keyboard_or_rows]
    return []


# ==============================================================================
# Per-Message Inline Action Buttons
# ==============================================================================

def get_standard_inline_action_buttons() -> list[dict[str, str]]:
    """Return the canonical four inline action buttons beneath bot messages."""
    return [
        {"text": LABEL_PERSIA, "callback_data": CALLBACK_PERSIA},
        {"text": LABEL_ENGLISH, "callback_data": CALLBACK_ENGLISH},
        {"text": LABEL_WORDS, "callback_data": CALLBACK_WORDS},
        {"text": LABEL_KNOWLEDGE, "callback_data": CALLBACK_KNOWLEDGE},
    ]


def has_four_inline_action_buttons(inline_keyboard_or_markup: Any) -> bool:
    """Check if all four inline action buttons (Persian, English, Words, Knowledge) are present."""
    rows = _extract_keyboard_rows(inline_keyboard_or_markup)
    has_fa = False
    has_en = False
    has_words = False
    has_knowledge = False

    for row in rows:
        for btn in row:
            if is_persian_button(btn):
                has_fa = True
            elif is_english_button(btn):
                has_en = True
            elif is_words_button(btn):
                has_words = True
            elif is_knowledge_button(btn):
                has_knowledge = True

    return has_fa and has_en and has_words and has_knowledge


def ensure_inline_action_buttons(
    markup_or_rows: Any = None,
) -> dict[str, Any] | InlineKeyboardMarkup:
    """Idempotently attach the 4 inline action buttons to an inline keyboard.

    - Preserves existing custom inline buttons above the action buttons.
    - If all 4 action buttons already exist, does not duplicate them.
    - If some action buttons are already present, appends only the missing ones.
    - Returns in dict format (or InlineKeyboardMarkup if passed as such).
    """
    is_object = isinstance(markup_or_rows, InlineKeyboardMarkup)

    if markup_or_rows is None:
        res = {"inline_keyboard": [get_standard_inline_action_buttons()]}
        return InlineKeyboardMarkup(inline_keyboard=res["inline_keyboard"]) if is_object else res

    raw_rows = _extract_keyboard_rows(markup_or_rows)

    has_fa = False
    has_en = False
    has_words = False
    has_knowledge = False

    for row in raw_rows:
        for btn in row:
            if is_persian_button(btn):
                has_fa = True
            elif is_english_button(btn):
                has_en = True
            elif is_words_button(btn):
                has_words = True
            elif is_knowledge_button(btn):
                has_knowledge = True

    # If all 4 already exist, return unmodified
    if has_fa and has_en and has_words and has_knowledge:
        if is_object:
            return markup_or_rows
        if isinstance(markup_or_rows, dict) and "inline_keyboard" in markup_or_rows:
            return markup_or_rows
        return {"inline_keyboard": raw_rows}

    # Deep-copy existing rows
    clean_rows: list[list[Any]] = []
    for row in raw_rows:
        row_copy = []
        for btn in row:
            if isinstance(btn, dict):
                row_copy.append(dict(btn))
            elif isinstance(btn, InlineKeyboardButton):
                row_copy.append(btn.to_dict())
            else:
                row_copy.append(btn)
        clean_rows.append(row_copy)

    # Determine missing action buttons
    missing_buttons: list[dict[str, str]] = []
    if not has_fa:
        missing_buttons.append({"text": LABEL_PERSIA, "callback_data": CALLBACK_PERSIA})
    if not has_en:
        missing_buttons.append({"text": LABEL_ENGLISH, "callback_data": CALLBACK_ENGLISH})
    if not has_words:
        missing_buttons.append({"text": LABEL_WORDS, "callback_data": CALLBACK_WORDS})
    if not has_knowledge:
        missing_buttons.append({"text": LABEL_KNOWLEDGE, "callback_data": CALLBACK_KNOWLEDGE})

    if missing_buttons:
        clean_rows.append(missing_buttons)

    if is_object:
        return InlineKeyboardMarkup(inline_keyboard=clean_rows)
    return {"inline_keyboard": clean_rows}


def build_inline_keyboard(
    buttons: list[list[Any]] | None = None,
    include_action_buttons: bool = True,
) -> dict[str, Any]:
    """Construct an inline keyboard with optional custom buttons and standard action row."""
    rows: list[list[Any]] = []
    if buttons:
        for r in buttons:
            rows.append(list(r))
    if include_action_buttons:
        return ensure_inline_action_buttons(rows)
    return {"inline_keyboard": rows}


# ==============================================================================
# Bottom Reply Keyboard Handling & Filtering
# ==============================================================================

def filter_reply_keyboard(
    keyboard_or_markup: Any,
    is_main_menu: bool | None = None,
) -> Any:
    """Filter reply keyboard according to main-menu vs sub-menu rules.

    - Main Menu: Keeps all buttons (language switch + task/fleet buttons allowed).
    - Sub-Menu: Strips language switch buttons and task/fleet buttons, removing
      any rows that become empty.
    """
    if keyboard_or_markup is None:
        return None

    is_object = isinstance(keyboard_or_markup, ReplyKeyboardMarkup)
    rows = _extract_keyboard_rows(keyboard_or_markup)

    # Determine if this represents a main menu or sub-menu
    main_menu = is_main_menu_keyboard(rows, is_main_menu=is_main_menu)

    if main_menu:
        if is_object:
            return keyboard_or_markup
        if isinstance(keyboard_or_markup, dict):
            return copy.deepcopy(keyboard_or_markup)
        return {"keyboard": copy.deepcopy(rows), "resize_keyboard": True}

    # Sub-menu / secondary screens: strip MAIN_MENU_ONLY_BUTTONS
    filtered_rows: list[list[Any]] = []
    for row in rows:
        clean_row: list[Any] = []
        for btn in row:
            if not is_main_menu_only_button(btn):
                if isinstance(btn, dict):
                    clean_row.append(dict(btn))
                elif isinstance(btn, KeyboardButton):
                    clean_row.append(btn.to_dict())
                else:
                    clean_row.append(btn)
        if clean_row:
            filtered_rows.append(clean_row)

    if is_object:
        return ReplyKeyboardMarkup(
            keyboard=filtered_rows,
            resize_keyboard=getattr(keyboard_or_markup, "resize_keyboard", True),
            one_time_keyboard=getattr(keyboard_or_markup, "one_time_keyboard", False),
        )

    res: dict[str, Any] = {"keyboard": filtered_rows}
    if isinstance(keyboard_or_markup, dict):
        for k, v in keyboard_or_markup.items():
            if k != "keyboard":
                res[k] = v
        if "resize_keyboard" not in res:
            res["resize_keyboard"] = True
    else:
        res["resize_keyboard"] = True

    return res


def build_main_menu_reply_keyboard(
    base_rows: list[list[Any]] | None = None,
    include_fleet_buttons: bool = True,
    include_lang_buttons: bool = True,
) -> dict[str, Any]:
    """Build a standard main home menu ReplyKeyboardMarkup.

    Includes base navigation rows, task/fleet buttons, and bottom language switch row.
    """
    rows: list[list[Any]] = []
    if base_rows:
        for r in base_rows:
            rows.append(list(r))

    if include_fleet_buttons:
        fleet_row = [
            {"text": "کار برای فلش"},
            {"text": "کار برای فلش به ناوگان"},
            {"text": "آرگون به ناوگان"},
        ]
        existing_texts = {extract_button_text(b) for row in rows for b in row}
        if not any(fb["text"] in existing_texts for fb in fleet_row):
            rows.append(fleet_row)

    if include_lang_buttons:
        lang_row = [
            {"text": "🇮🇷 FA"},
            {"text": "🇬🇧 EN"},
        ]
        existing_texts = {extract_button_text(b) for row in rows for b in row}
        if not any(lb["text"] in existing_texts for lb in lang_row):
            rows.append(lang_row)

    return {"keyboard": rows, "resize_keyboard": True}


def build_sub_menu_reply_keyboard(
    keyboard_or_rows: Any,
) -> dict[str, Any]:
    """Build a sub-menu ReplyKeyboardMarkup guaranteed to have NO main-menu-only buttons."""
    return filter_reply_keyboard(keyboard_or_rows, is_main_menu=False)


# ==============================================================================
# Outgoing Message Processing & Dispatch Helpers
# ==============================================================================

def process_outgoing_message(
    message: dict[str, Any],
    is_main_menu: bool | None = None,
) -> dict[str, Any]:
    """Process an outgoing Telegram Bot message payload.

    Guarantees:
    1. If `reply_markup` is an InlineKeyboardMarkup, the 4 inline action buttons
       are attached idempotently.
    2. If `reply_markup` is a ReplyKeyboardMarkup, it is filtered so sub-menus
       never contain language or task/fleet buttons.
    3. If `reply_markup` is absent or None, an InlineKeyboardMarkup containing
       the 4 standard action buttons is attached.
    """
    msg = dict(message)
    reply_markup = msg.get("reply_markup")

    if reply_markup is None:
        msg["reply_markup"] = ensure_inline_action_buttons()
        return msg

    if isinstance(reply_markup, dict):
        if "inline_keyboard" in reply_markup:
            msg["reply_markup"] = ensure_inline_action_buttons(reply_markup)
        elif "keyboard" in reply_markup:
            msg["reply_markup"] = filter_reply_keyboard(reply_markup, is_main_menu=is_main_menu)
    elif isinstance(reply_markup, InlineKeyboardMarkup):
        msg["reply_markup"] = ensure_inline_action_buttons(reply_markup)
    elif isinstance(reply_markup, ReplyKeyboardMarkup):
        msg["reply_markup"] = filter_reply_keyboard(reply_markup, is_main_menu=is_main_menu)
    elif hasattr(reply_markup, "inline_keyboard"):
        msg["reply_markup"] = ensure_inline_action_buttons(reply_markup)
    elif hasattr(reply_markup, "keyboard"):
        msg["reply_markup"] = filter_reply_keyboard(reply_markup, is_main_menu=is_main_menu)

    return msg


def attach_inline_buttons(
    markup_or_message: Any,
    is_main_menu: bool | None = None,
) -> Any:
    """Wrapper function to attach 4 inline action buttons to markup or message."""
    if isinstance(markup_or_message, dict) and ("text" in markup_or_message or "chat_id" in markup_or_message):
        return process_outgoing_message(markup_or_message, is_main_menu=is_main_menu)
    return ensure_inline_action_buttons(markup_or_message)


# ==============================================================================
# Callback Query Handler
# ==============================================================================

def handle_bilingual_callback(
    callback_data: str,
    user_id: int | str | None = None,
) -> dict[str, Any]:
    """Handle callback queries for the 4 per-message action buttons.

    Returns response dictionary with action type and text/notice.
    """
    cb = str(callback_data or "").strip()

    if any(cb == alias or cb.startswith(alias + ":") or cb.startswith(alias + "_") for alias in CALLBACK_PERSIA_ALIASES):
        return {
            "status": "ok",
            "action": "switch_language",
            "lang": "fa",
            "alert": "زبان به فارسی تنظیم شد 🇮🇷",
        }

    if any(cb == alias or cb.startswith(alias + ":") or cb.startswith(alias + "_") for alias in CALLBACK_ENGLISH_ALIASES):
        return {
            "status": "ok",
            "action": "switch_language",
            "lang": "en",
            "alert": "Language switched to English 🇬🇧",
        }

    if any(cb == alias or cb.startswith(alias + ":") or cb.startswith(alias + "_") for alias in CALLBACK_WORDS_ALIASES):
        return {
            "status": "ok",
            "action": "words_lookup",
            "alert": "📘 واژه‌نامه / Words Glossary",
        }

    if any(cb == alias or cb.startswith(alias + ":") or cb.startswith(alias + "_") for alias in CALLBACK_KNOWLEDGE_ALIASES):
        return {
            "status": "ok",
            "action": "knowledge_lookup",
            "alert": "🧠 پایگاه دانش / Knowledge Base",
        }

    return {
        "status": "ignored",
        "action": None,
        "alert": None,
    }
