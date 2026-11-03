import copy
import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from hq.private_update_auth import authorize_private_update as authorize

OWNER = {'telegram': '101', 'bale': '202'}
MSG = {'update_id': 1, 'message': {'chat': {'type': 'private', 'id': 101}, 'from': {'id': 101, 'is_bot': False}, 'text': '/status'}}
CALLBACK = {'update_id': 2, 'callback_query': {'from': {'id': 101, 'is_bot': False}, 'message': {'chat': {'type': 'private', 'id': 101}}, 'data': 'status'}}

class PrivateUpdateAuthTests(unittest.TestCase):
    def test_owner_private_message_and_callback(self):
        for update in (MSG, CALLBACK): self.assertTrue(authorize('telegram', update, OWNER, True))
    def test_unverified_transport_and_invalid_platform(self):
        for verified in (False, None, 1, 'true'):
            self.assertFalse(authorize('telegram', MSG, OWNER, verified))
        for platform in ('bale', 'Telegram', None):
            self.assertFalse(authorize(platform, MSG, OWNER, True))
    def test_owner_chat_other_sender_and_inverse_rejected(self):
        a = copy.deepcopy(MSG); a['message']['from']['id'] = 999
        b = copy.deepcopy(MSG); b['message']['chat']['id'] = 999
        for update in (a, b): self.assertFalse(authorize('telegram', update, OWNER, True))
    def test_callback_uses_callback_sender_not_message_sender(self):
        c = copy.deepcopy(CALLBACK); c['callback_query']['from']['id'] = 999
        c['callback_query']['message']['from'] = {'id': 101, 'is_bot': False}
        self.assertFalse(authorize('telegram', c, OWNER, True))
    def test_group_channel_and_sender_chat_rejected(self):
        for typ in ('group', 'supergroup', 'channel', None):
            m = copy.deepcopy(MSG); m['message']['chat']['type'] = typ
            self.assertFalse(authorize('telegram', m, OWNER, True))
        m = copy.deepcopy(MSG); m['message']['sender_chat'] = {'id': 101}
        self.assertFalse(authorize('telegram', m, OWNER, True))
    def test_bot_or_missing_bot_marker_rejected(self):
        for marker in (True, None, 0, 'false'):
            m = copy.deepcopy(MSG); m['message']['from']['is_bot'] = marker
            self.assertFalse(authorize('telegram', m, OWNER, True))
    def test_unsupported_ambiguous_and_malformed_updates(self):
        samples = [None, {}, {'edited_message': MSG['message']}, {'message': None},
                   {'callback_query': {'from': MSG['message']['from']}},
                   {**MSG, 'callback_query': CALLBACK['callback_query']},
                   {**MSG, 'channel_post': {}}]
        for u in samples: self.assertFalse(authorize('telegram', u, OWNER, True))
    def test_malformed_ids_and_configuration_fail_closed(self):
        for value in (None, True, 0, -1, 1.0, '0101', '+101', ' 101', '١٠١', str(2**53), {}, []):
            m = copy.deepcopy(MSG); m['message']['from']['id'] = value
            self.assertFalse(authorize('telegram', m, OWNER, True))
            self.assertFalse(authorize('telegram', MSG, {'telegram': value}, True))
        for config in (None, {}, {'bale': '101'}):
            self.assertFalse(authorize('telegram', MSG, config, True))
    def test_string_or_number_id_consistency(self):
        m = copy.deepcopy(MSG); m['message']['chat']['id'] = '101'; m['message']['from']['id'] = '101'
        self.assertTrue(authorize('telegram', m, {'telegram': 101}, True))

if __name__ == '__main__': unittest.main()
