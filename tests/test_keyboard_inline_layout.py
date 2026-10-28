"""test_keyboard_inline_layout.py.

Comprehensive test suite verifying P10 requirements in agw-workers:
1. Test 1 (Main Home Menu Reply Keyboard):
   - Language switch buttons ('فارسی' / 'انگلیسی' or '🇮🇷 FA' / '🇬🇧 EN') and
     task/fleet buttons ('کار برای فلش', 'کار برای فلش به ناوگان', 'آرگون به ناوگان')
     appear ONLY on the main home menu screen ('صفحهٔ اصلی') in ReplyKeyboardMarkup.
2. Test 2 (Sub-Menu Reply Keyboards):
   - Sub-menu builders in hq_service.py and filtering logic in rk_proxy.py and bilingual.py
     strictly omit/strip 'فارسی', 'انگلیسی', '🇮🇷 FA', '🇬🇧 EN', 'کار برای فلش',
     'کار برای فلش به ناوگان', and 'آرگون به ناوگان'.
3. Test 3 (Per-Message Inline Action Buttons):
   - Every bot message consistently receives the 4 inline action buttons:
     Persian ('🇮🇷 FA'), English ('🇬🇧 EN'), Words ('📘 Words'), Knowledge ('Knowledge').
   - Pipelines are idempotent (no duplicate inline button rows).
"""

from __future__ import annotations

import copy
import pytest

import bilingual
import hq_service
import rk_proxy


# ==============================================================================
# Helper Verification Functions
# ==============================================================================

FORBIDDEN_SUBMENU_BUTTONS = {
    "فارسی",
    "انگلیسی",
    "🇮🇷 FA",
    "🇬🇧 EN",
    "🇮🇷 فارسی",
    "🇬🇧 انگلیسی",
    "FA",
    "EN",
    "کار برای فلش",
    "کار برای فلش به ناوگان",
    "آرگون به ناوگان",
}

REQUIRED_FLEET_BUTTONS = {
    "کار برای فلش",
    "کار برای فلش به ناوگان",
    "آرگون به ناوگان",
}


def _extract_button_texts_from_keyboard(keyboard_or_rows) -> list[str]:
    """Flatten 2D keyboard into list of button text strings."""
    if isinstance(keyboard_or_rows, dict):
        rows = keyboard_or_rows.get("keyboard", [])
    elif hasattr(keyboard_or_rows, "keyboard"):
        rows = getattr(keyboard_or_rows, "keyboard", [])
    elif isinstance(keyboard_or_rows, list):
        rows = keyboard_or_rows
    else:
        return []

    texts: list[str] = []
    for row in rows:
        row_list = row if isinstance(row, list) else [row]
        for btn in row_list:
            if isinstance(btn, str):
                texts.append(btn.strip())
            elif isinstance(btn, dict):
                texts.append(str(btn.get("text", "")).strip())
            elif hasattr(btn, "text"):
                texts.append(str(getattr(btn, "text", "")).strip())
            else:
                texts.append(str(btn).strip())
    return texts


def _extract_inline_buttons(markup_or_message) -> list[dict[str, str]]:
    """Extract list of inline button dicts from message or markup."""
    if isinstance(markup_or_message, dict):
        if "reply_markup" in markup_or_message:
            return _extract_inline_buttons(markup_or_message["reply_markup"])
        if "inline_keyboard" in markup_or_message:
            rows = markup_or_message.get("inline_keyboard", [])
        else:
            return []
    elif hasattr(markup_or_message, "inline_keyboard"):
        rows = getattr(markup_or_message, "inline_keyboard", [])
    elif isinstance(markup_or_message, list):
        rows = markup_or_message
    else:
        return []

    flat: list[dict[str, str]] = []
    for row in rows:
        row_list = row if isinstance(row, list) else [row]
        for btn in row_list:
            if isinstance(btn, dict):
                flat.append({
                    "text": str(btn.get("text", "")).strip(),
                    "callback_data": str(btn.get("callback_data", "")).strip(),
                })
            elif hasattr(btn, "text"):
                flat.append({
                    "text": str(getattr(btn, "text", "")).strip(),
                    "callback_data": str(getattr(btn, "callback_data", "")).strip(),
                })
    return flat


def _has_all_four_inline_action_buttons(buttons: list[dict[str, str]]) -> bool:
    """Verify presence of Persian, English, Words, and Knowledge buttons."""
    has_fa = any("FA" in b["text"] or "فارسی" in b["text"] or "fa" in b["callback_data"].lower() for b in buttons)
    has_en = any("EN" in b["text"] or "انگلیسی" in b["text"] or "en" in b["callback_data"].lower() for b in buttons)
    has_words = any("Words" in b["text"] or "words" in b["callback_data"].lower() for b in buttons)
    has_knowledge = any("Knowledge" in b["text"] or "knowledge" in b["callback_data"].lower() for b in buttons)
    return has_fa and has_en and has_words and has_knowledge


# ==============================================================================
# TEST 1: Main Home Menu Reply Keyboard
# ==============================================================================

class TestMainHomeMenuReplyKeyboard:
    """Verifies that the main home menu contains task/fleet and language buttons."""

    def test_hq_service_build_main_menu_keyboard_fa(self):
        """Main home menu in Persian must contain fleet buttons and FA/EN language buttons."""
        kb = hq_service.build_main_menu_keyboard(lang="fa")
        texts = _extract_button_texts_from_keyboard(kb)

        # 1. Fleet buttons present
        for fleet_btn in REQUIRED_FLEET_BUTTONS:
            assert fleet_btn in texts, f"Expected fleet button '{fleet_btn}' in main menu"

        # 2. Language switch buttons present
        assert any(b in ("🇮🇷 FA", "فارسی", "🇮🇷 فارسی", "FA") for b in texts), "Persian button missing"
        assert any(b in ("🇬🇧 EN", "انگلیسی", "🇬🇧 انگلیسی", "EN") for b in texts), "English button missing"

        # 3. Recognized as main menu by all modules
        assert hq_service.is_main_menu_keyboard(kb) is True
        assert rk_proxy.is_main_menu_keyboard(kb) is True
        assert bilingual.is_main_menu_keyboard(kb) is True

    def test_hq_service_build_main_menu_keyboard_en(self):
        """Main home menu in English must contain fleet buttons and FA/EN language buttons."""
        kb = hq_service.build_main_menu_keyboard(lang="en")
        texts = _extract_button_texts_from_keyboard(kb)

        for fleet_btn in REQUIRED_FLEET_BUTTONS:
            assert fleet_btn in texts, f"Expected fleet button '{fleet_btn}' in main menu (EN)"

        assert "🇮🇷 FA" in texts or "FA" in texts
        assert "🇬🇧 EN" in texts or "EN" in texts
        assert hq_service.is_main_menu_keyboard(kb) is True
        assert rk_proxy.is_main_menu_keyboard(kb) is True
        assert bilingual.is_main_menu_keyboard(kb) is True

    def test_bilingual_build_main_menu_reply_keyboard(self):
        """bilingual.py builder must include fleet and language buttons."""
        kb = bilingual.build_main_menu_reply_keyboard(
            base_rows=[[{"text": "📋 مدیریت وظایف"}, {"text": "📊 وضعیت ناوگان"}]],
            include_fleet_buttons=True,
            include_lang_buttons=True,
        )
        texts = _extract_button_texts_from_keyboard(kb)
        for fleet_btn in REQUIRED_FLEET_BUTTONS:
            assert fleet_btn in texts
        assert "🇮🇷 FA" in texts
        assert "🇬🇧 EN" in texts
        assert bilingual.is_main_menu_keyboard(kb) is True

    def test_rk_proxy_ensure_main_menu_reply_keyboard(self):
        """rk_proxy.py must inject fleet and language buttons if missing from main menu."""
        base_rows = [["📋 مدیریت وظایف", "📊 وضعیت ناوگان"]]
        enriched = rk_proxy.ensure_main_menu_reply_keyboard(base_rows)
        texts = _extract_button_texts_from_keyboard(enriched)
        for fleet_btn in REQUIRED_FLEET_BUTTONS:
            assert fleet_btn in texts
        assert any("FA" in t for t in texts)
        assert any("EN" in t for t in texts)

    def test_ensure_main_menu_buttons_is_idempotent(self):
        """Ensuring main menu buttons repeatedly must never duplicate fleet or language rows."""
        kb = hq_service.build_main_menu_keyboard(lang="fa")
        original_row_count = len(kb["keyboard"])

        # Call ensure_main_menu_buttons on already-complete keyboard
        enriched1 = hq_service.ensure_main_menu_buttons(kb)
        enriched2 = hq_service.ensure_main_menu_buttons(enriched1)
        assert len(enriched2["keyboard"]) == original_row_count

        # Same for rk_proxy
        rk_enriched1 = rk_proxy.ensure_main_menu_reply_keyboard(kb["keyboard"])
        rk_enriched2 = rk_proxy.ensure_main_menu_reply_keyboard(rk_enriched1)
        assert len(rk_enriched2) == original_row_count

    def test_text_navigation_to_main_menu(self):
        """Handling 'صفحه اصلی' or 'Back' command must return main menu with fleet + lang buttons."""
        svc = hq_service.HQService()
        for cmd in ["صفحه اصلی", "صفحهٔ اصلی", "منوی اصلی", "Back", "Main Menu"]:
            resp = svc.handle_text_message(cmd, user_id=12345)
            rk = resp.get("reply_keyboard") or resp.get("reply_markup")
            texts = _extract_button_texts_from_keyboard(rk)
            for fleet_btn in REQUIRED_FLEET_BUTTONS:
                assert fleet_btn in texts, f"Expected fleet button '{fleet_btn}' on navigating via '{cmd}'"
            assert any("FA" in t or "فارسی" in t for t in texts)
            assert any("EN" in t or "انگلیسی" in t for t in texts)


# ==============================================================================
# TEST 2: Sub-Menu Reply Keyboards
# ==============================================================================

class TestSubMenuReplyKeyboards:
    """Verifies that all sub-menus and secondary screens strictly omit main-menu-only buttons."""

    @pytest.mark.parametrize("builder_fn", [
        hq_service.build_tasks_menu_keyboard,
        hq_service.build_fleet_menu_keyboard,
        hq_service.build_settings_menu_keyboard,
        hq_service.build_help_menu_keyboard,
    ])
    def test_hq_service_submenu_builders_fa_and_en(self, builder_fn):
        """Submenu builders in hq_service must contain NO forbidden main-menu buttons."""
        for lang in ["fa", "en"]:
            kb = builder_fn(lang=lang)
            texts = _extract_button_texts_from_keyboard(kb)
            for forbidden in FORBIDDEN_SUBMENU_BUTTONS:
                assert forbidden not in texts, f"Forbidden button '{forbidden}' found in submenu for {builder_fn.__name__} ({lang})"

            # Must be recognized as submenu (not main menu)
            assert hq_service.is_main_menu_keyboard(kb) is False
            assert rk_proxy.is_main_menu_keyboard(kb) is False
            assert bilingual.is_main_menu_keyboard(kb) is False

    def test_hq_service_generic_build_sub_menu_keyboard(self):
        """Generic build_sub_menu_keyboard strips any mistakenly passed main-menu buttons."""
        dirty_items = [
            ["Option A", "کار برای فلش"],
            ["Option B", "🇮🇷 FA"],
            ["آرگون به ناوگان", "Option C"],
        ]
        clean_kb = hq_service.build_sub_menu_keyboard(dirty_items)
        texts = _extract_button_texts_from_keyboard(clean_kb)
        for forbidden in FORBIDDEN_SUBMENU_BUTTONS:
            assert forbidden not in texts, f"Forbidden button '{forbidden}' survived filtering"
        assert "Option A" in texts
        assert "Option B" in texts
        assert "Option C" in texts
        assert hq_service.is_main_menu_keyboard(clean_kb) is False

    def test_bilingual_filter_reply_keyboard_on_dirty_submenu(self):
        """bilingual.filter_reply_keyboard must strip fleet & language buttons from submenus."""
        dirty_markup = {
            "keyboard": [
                [{"text": "ویرایش پروفایل"}, {"text": "تغییر رمز"}],
                [{"text": "کار برای فلش"}, {"text": "کار برای فلش به ناوگان"}],
                [{"text": "🇮🇷 FA"}, {"text": "🇬🇧 EN"}],
                [{"text": "🔙 بازگشت"}],
            ],
            "resize_keyboard": True,
        }
        filtered = bilingual.filter_reply_keyboard(dirty_markup, is_main_menu=False)
        texts = _extract_button_texts_from_keyboard(filtered)

        for forbidden in FORBIDDEN_SUBMENU_BUTTONS:
            assert forbidden not in texts, f"bilingual.filter_reply_keyboard failed to strip '{forbidden}'"

        assert "ویرایش پروفایل" in texts
        assert "تغییر رمز" in texts
        assert "🔙 بازگشت" in texts
        # The rows that had ONLY forbidden buttons must be pruned completely
        assert len(filtered["keyboard"]) == 2

    def test_rk_proxy_filter_submenu_reply_keyboard(self):
        """rk_proxy.filter_submenu_reply_keyboard must strip fleet & language buttons."""
        dirty_rows = [
            ["📌 تنظیمات کاربری", "کار برای فلش"],
            ["آرگون به ناوگان"],
            ["فارسی", "انگلیسی"],
            ["🔙 منوی اصلی"],
        ]
        clean_rows = rk_proxy.filter_submenu_reply_keyboard(dirty_rows)
        texts = _extract_button_texts_from_keyboard(clean_rows)

        for forbidden in FORBIDDEN_SUBMENU_BUTTONS:
            assert forbidden not in texts, f"rk_proxy.filter_submenu_reply_keyboard failed to strip '{forbidden}'"

        assert "📌 تنظیمات کاربری" in texts
        assert "🔙 منوی اصلی" in texts
        # The two rows that had only forbidden buttons must be pruned
        assert len(clean_rows) == 2

    def test_text_navigation_to_submenus(self):
        """Triggering submenus via text messages must yield keyboards with NO main-menu buttons."""
        svc = hq_service.HQService()
        submenu_triggers = [
            "📋 مدیریت وظایف",
            "📊 وضعیت ناوگان",
            "⚙️ تنظیمات",
            "ℹ️ راهنما",
        ]
        for trigger in submenu_triggers:
            resp = svc.handle_text_message(trigger, user_id=999)
            rk = resp.get("reply_keyboard") or resp.get("reply_markup")
            texts = _extract_button_texts_from_keyboard(rk)
            for forbidden in FORBIDDEN_SUBMENU_BUTTONS:
                assert forbidden not in texts, f"Submenu triggered by '{trigger}' contained '{forbidden}'"


# ==============================================================================
# TEST 3: Per-Message Inline Action Buttons
# ==============================================================================

class TestPerMessageInlineActionButtons:
    """Verifies that every bot message receives the 4 inline action buttons idempotently."""

    def test_standard_inline_action_buttons_definition(self):
        """Check the 4 standard buttons across bilingual, rk_proxy, and hq_service."""
        # bilingual.py
        b_btns = bilingual.get_standard_inline_action_buttons()
        assert len(b_btns) == 4
        assert [b["text"] for b in b_btns] == ["🇮🇷 FA", "🇬🇧 EN", "📘 Words", "Knowledge"]
        assert [b["callback_data"] for b in b_btns] == [
            "bilingual_fa:1", "bilingual_en:1", "bilingual_words:1", "bilingual_knowledge:1"
        ]

        # rk_proxy.py
        p_btns = rk_proxy.STANDARD_INLINE_ACTIONS
        assert len(p_btns) == 4
        assert [b["text"] for b in p_btns] == ["🇮🇷 FA", "🇬🇧 EN", "📘 Words", "Knowledge"]
        assert [b["callback_data"] for b in p_btns] == [
            "bilingual_fa:1", "bilingual_en:1", "bilingual_words:1", "bilingual_knowledge:1"
        ]

        # hq_service.py
        h_btns = hq_service.get_standard_inline_action_row()
        assert len(h_btns) == 4
        assert [b["text"] for b in h_btns] == ["🇮🇷 FA", "🇬🇧 EN", "📘 Words", "Knowledge"]
        assert [b["callback_data"] for b in h_btns] == [
            "bilingual_fa:1", "bilingual_en:1", "bilingual_words:1", "bilingual_knowledge:1"
        ]

    def test_plain_text_message_receives_four_inline_buttons(self):
        """Plain text message (no initial reply_markup) must receive all 4 inline buttons."""
        # 1. hq_service.prepare_message / send_message
        svc = hq_service.HQService()
        msg_hq = svc.prepare_message(chat_id=123, text="Test message")
        inline_hq = _extract_inline_buttons(msg_hq)
        assert len(inline_hq) == 4
        assert _has_all_four_inline_action_buttons(inline_hq)

        # 2. bilingual.process_outgoing_message
        msg_bi = bilingual.process_outgoing_message({"chat_id": 123, "text": "Test message"})
        inline_bi = _extract_inline_buttons(msg_bi)
        assert len(inline_bi) == 4
        assert _has_all_four_inline_action_buttons(inline_bi)

        # 3. rk_proxy.process_telegram_payload
        msg_proxy = rk_proxy.process_telegram_payload({"chat_id": 123, "text": "Test message"})
        inline_proxy = _extract_inline_buttons(msg_proxy)
        assert len(inline_proxy) == 4
        assert _has_all_four_inline_action_buttons(inline_proxy)

    def test_message_with_custom_inline_buttons_preserves_custom_and_appends_actions(self):
        """Existing custom inline buttons must be preserved above the 4 standard action buttons."""
        custom_markup = {
            "inline_keyboard": [
                [{"text": "🔍 مشاهده جزئیات", "callback_data": "view_details"}],
                [{"text": "🔗 لینک خارجی", "url": "https://example.com"}],
            ]
        }

        # 1. hq_service
        svc = hq_service.HQService()
        msg_hq = svc.prepare_message(chat_id=123, text="Custom", reply_markup=custom_markup)
        inline_hq = _extract_inline_buttons(msg_hq)
        assert len(inline_hq) == 6
        assert inline_hq[0]["text"] == "🔍 مشاهده جزئیات"
        assert inline_hq[1]["text"] == "🔗 لینک خارجی"
        assert _has_all_four_inline_action_buttons(inline_hq[2:])

        # 2. bilingual
        msg_bi = bilingual.process_outgoing_message({"chat_id": 123, "text": "Custom", "reply_markup": custom_markup})
        inline_bi = _extract_inline_buttons(msg_bi)
        assert len(inline_bi) == 6
        assert inline_bi[0]["text"] == "🔍 مشاهده جزئیات"
        assert inline_bi[1]["text"] == "🔗 لینک خارجی"
        assert _has_all_four_inline_action_buttons(inline_bi[2:])

        # 3. rk_proxy
        msg_proxy = rk_proxy.process_telegram_payload({"chat_id": 123, "text": "Custom", "reply_markup": custom_markup})
        inline_proxy = _extract_inline_buttons(msg_proxy)
        assert len(inline_proxy) == 6
        assert inline_proxy[0]["text"] == "🔍 مشاهده جزئیات"
        assert inline_proxy[1]["text"] == "🔗 لینک خارجی"
        assert _has_all_four_inline_action_buttons(inline_proxy[2:])

    def test_message_with_partial_action_buttons_only_adds_missing(self):
        """If some action buttons already exist, pipelines must NOT duplicate them."""
        partial_markup = {
            "inline_keyboard": [
                [{"text": "🇮🇷 FA", "callback_data": "bilingual_fa:1"}, {"text": "🇬🇧 EN", "callback_data": "bilingual_en:1"}]
            ]
        }

        # 1. bilingual
        bi_res = bilingual.ensure_inline_action_buttons(partial_markup)
        bi_flat = _extract_inline_buttons(bi_res)
        assert len(bi_flat) == 4
        assert _has_all_four_inline_action_buttons(bi_flat)
        fa_count = sum(1 for b in bi_flat if "FA" in b["text"])
        en_count = sum(1 for b in bi_flat if "EN" in b["text"])
        assert fa_count == 1
        assert en_count == 1

        # 2. rk_proxy
        proxy_res = rk_proxy.build_inline_action_keyboard(partial_markup["inline_keyboard"])
        proxy_flat = _extract_inline_buttons(proxy_res)
        assert len(proxy_flat) == 4
        assert _has_all_four_inline_action_buttons(proxy_flat)
        assert sum(1 for b in proxy_flat if "FA" in b["text"]) == 1
        assert sum(1 for b in proxy_flat if "EN" in b["text"]) == 1

        # 3. hq_service
        hq_res = hq_service.ensure_inline_action_buttons(partial_markup)
        hq_flat = _extract_inline_buttons(hq_res)
        assert len(hq_flat) == 4
        assert _has_all_four_inline_action_buttons(hq_flat)
        assert sum(1 for b in hq_flat if "FA" in b["text"]) == 1
        assert sum(1 for b in hq_flat if "EN" in b["text"]) == 1

    def test_message_with_main_menu_reply_keyboard_receives_four_inline_buttons(self):
        """Message sent with main-menu reply keyboard must also deliver the 4 inline action buttons."""
        main_rk = hq_service.build_main_menu_keyboard(lang="fa")

        # 1. hq_service
        svc = hq_service.HQService()
        msg_hq = svc.prepare_message(chat_id=123, text="Main Menu", reply_markup=main_rk, is_main_menu=True)
        inline_hq = _extract_inline_buttons(msg_hq)
        assert len(inline_hq) == 4
        assert _has_all_four_inline_action_buttons(inline_hq)
        # Reply keyboard preserved with fleet and language buttons
        rk_texts = _extract_button_texts_from_keyboard(msg_hq.get("reply_keyboard") or msg_hq.get("reply_markup"))
        for fb in REQUIRED_FLEET_BUTTONS:
            assert fb in rk_texts

        # 2. rk_proxy payload splitting
        split_payloads = rk_proxy.split_payload_for_telegram(
            {"chat_id": 123, "text": "Main Menu", "reply_markup": main_rk},
            is_main_menu=True,
        )
        assert len(split_payloads) == 2
        # First payload delivers inline action buttons
        inline_first = _extract_inline_buttons(split_payloads[0])
        assert len(inline_first) == 4
        assert _has_all_four_inline_action_buttons(inline_first)
        # Second payload delivers main menu reply keyboard
        rk_second = _extract_button_texts_from_keyboard(split_payloads[1]["reply_markup"])
        for fb in REQUIRED_FLEET_BUTTONS:
            assert fb in rk_second

        # 3. bilingual
        msg_bi = bilingual.process_outgoing_message(
            {"chat_id": 123, "text": "Main Menu", "reply_markup": main_rk},
            is_main_menu=True,
        )
        inline_bi = _extract_inline_buttons(msg_bi)
        assert len(inline_bi) == 4
        assert _has_all_four_inline_action_buttons(inline_bi)

    def test_message_with_sub_menu_reply_keyboard_receives_four_inline_buttons(self):
        """Message sent with sub-menu reply keyboard must also deliver the 4 inline action buttons."""
        sub_rk = hq_service.build_tasks_menu_keyboard(lang="fa")

        # 1. hq_service
        svc = hq_service.HQService()
        msg_hq = svc.prepare_message(chat_id=123, text="Tasks Submenu", reply_markup=sub_rk, is_main_menu=False)
        inline_hq = _extract_inline_buttons(msg_hq)
        assert len(inline_hq) == 4
        assert _has_all_four_inline_action_buttons(inline_hq)
        # Sub-menu ReplyKeyboard is clean
        rk_texts = _extract_button_texts_from_keyboard(msg_hq.get("reply_keyboard") or msg_hq.get("reply_markup"))
        for forbidden in FORBIDDEN_SUBMENU_BUTTONS:
            assert forbidden not in rk_texts

        # 2. rk_proxy
        split_payloads = rk_proxy.split_payload_for_telegram(
            {"chat_id": 123, "text": "Tasks Submenu", "reply_markup": sub_rk},
            is_main_menu=False,
        )
        assert len(split_payloads) == 2
        inline_first = _extract_inline_buttons(split_payloads[0])
        assert len(inline_first) == 4
        assert _has_all_four_inline_action_buttons(inline_first)
        rk_second = _extract_button_texts_from_keyboard(split_payloads[1]["reply_markup"])
        for forbidden in FORBIDDEN_SUBMENU_BUTTONS:
            assert forbidden not in rk_second

        # 3. bilingual
        msg_bi = bilingual.process_outgoing_message(
            {"chat_id": 123, "text": "Tasks Submenu", "reply_markup": sub_rk},
            is_main_menu=False,
        )
        inline_bi = _extract_inline_buttons(msg_bi)
        assert len(inline_bi) == 4
        assert _has_all_four_inline_action_buttons(inline_bi)
        rk_bi = _extract_button_texts_from_keyboard(msg_bi.get("reply_keyboard") or msg_bi.get("reply_markup"))
        for forbidden in FORBIDDEN_SUBMENU_BUTTONS:
            assert forbidden not in rk_bi

    def test_idempotency_of_inline_button_injection(self):
        """Running a message through the injection pipeline multiple times must not duplicate buttons."""
        raw_msg = {"chat_id": 100, "text": "Idempotency Test"}

        # bilingual.py
        bi_step1 = bilingual.process_outgoing_message(raw_msg)
        bi_step2 = bilingual.process_outgoing_message(bi_step1)
        bi_step3 = bilingual.process_outgoing_message(bi_step2)
        assert len(_extract_inline_buttons(bi_step3)) == 4

        # rk_proxy.py
        px_step1 = rk_proxy.process_telegram_payload(raw_msg)
        px_step2 = rk_proxy.process_telegram_payload(px_step1)
        px_step3 = rk_proxy.process_telegram_payload(px_step2)
        assert len(_extract_inline_buttons(px_step3)) == 4

        # hq_service.py
        svc = hq_service.HQService()
        hq_step1 = svc.prepare_message(chat_id=100, text="Idempotency Test")
        hq_step2 = svc.prepare_message(chat_id=100, text="Idempotency Test", reply_markup=hq_step1["reply_markup"])
        assert len(_extract_inline_buttons(hq_step2)) == 4


# ==============================================================================
# TEST 4: Callback Query Handling & Cross-Module Compatibility
# ==============================================================================

class TestCrossModuleAndCallbacks:
    """Verifies callback query handling and cross-module model compatibility."""

    @pytest.mark.parametrize("cb_data, expected_lang", [
        ("bilingual_fa:1", "fa"),
        ("lang_fa", "fa"),
        ("bilingual_en:1", "en"),
        ("lang_en", "en"),
    ])
    def test_language_switch_callbacks(self, cb_data, expected_lang):
        """Both hq_service and bilingual must handle language callbacks."""
        svc = hq_service.HQService()
        h_res = svc.handle_callback_query({"data": cb_data, "from": {"id": 555}})
        assert h_res.get("action") == "language_switched"
        assert h_res.get("lang") == expected_lang
        assert svc.get_user_language(555) == expected_lang

        b_res = bilingual.handle_bilingual_callback(cb_data, user_id=555)
        assert b_res.get("action") == "switch_language"
        assert b_res.get("lang") == expected_lang

    @pytest.mark.parametrize("cb_data, expected_action", [
        ("bilingual_words:1", "words"),
        ("words", "words"),
        ("bilingual_knowledge:1", "knowledge"),
        ("knowledge", "knowledge"),
    ])
    def test_words_and_knowledge_callbacks(self, cb_data, expected_action):
        """Words and Knowledge action callbacks must be handled."""
        svc = hq_service.HQService()
        h_res = svc.handle_callback_query({"data": cb_data, "from": {"id": 777}})
        assert expected_action in h_res.get("action")

        b_res = bilingual.handle_bilingual_callback(cb_data, user_id=777)
        assert expected_action in b_res.get("action")

    def test_dataclass_objects_compatibility_with_proxy(self):
        """bilingual dataclass objects (InlineKeyboardMarkup, ReplyKeyboardMarkup) work with rk_proxy."""
        # InlineKeyboardMarkup object
        btn1 = bilingual.InlineKeyboardButton(text="Custom", callback_data="btn_custom")
        markup_obj = bilingual.InlineKeyboardMarkup(inline_keyboard=[[btn1]])

        ensured_obj = bilingual.ensure_inline_action_buttons(markup_obj)
        assert isinstance(ensured_obj, bilingual.InlineKeyboardMarkup)
        flat = _extract_inline_buttons(ensured_obj)
        assert len(flat) == 5
        assert _has_all_four_inline_action_buttons(flat[1:])

        # Pass object rows to rk_proxy
        px_rows = rk_proxy.build_inline_action_keyboard(ensured_obj.inline_keyboard)
        assert len(px_rows) == 2
        assert len(_extract_inline_buttons(px_rows)) == 5

    def test_telegram_reply_keyboard_proxy_send_message(self):
        """TelegramReplyKeyboardProxy.send_message splits payloads and triggers sender_fn."""
        proxy = rk_proxy.TelegramReplyKeyboardProxy()
        dispatched: list[dict] = []

        def mock_sender(payload: dict) -> dict:
            dispatched.append(payload)
            return {"ok": True}

        main_rk = hq_service.build_main_menu_keyboard(lang="fa")
        res = proxy.send_message(
            chat_id=12345,
            text="Testing Proxy",
            reply_markup=main_rk,
            is_main_menu=True,
            sender_fn=mock_sender,
        )

        assert len(res) == 2
        assert len(dispatched) == 2
        # First message has 4 inline buttons
        assert _has_all_four_inline_action_buttons(_extract_inline_buttons(dispatched[0]))
        # Second message has main menu reply keyboard
        second_texts = _extract_button_texts_from_keyboard(dispatched[1]["reply_markup"])
        for fb in REQUIRED_FLEET_BUTTONS:
            assert fb in second_texts
