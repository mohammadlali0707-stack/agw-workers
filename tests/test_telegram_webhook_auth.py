import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from hq.telegram_webhook_auth import verify_telegram_webhook as verify

class TelegramWebhookAuthTests(unittest.TestCase):
    def test_matching_header_case_insensitive(self):
        self.assertTrue(verify([('X-Telegram-Bot-Api-Secret-Token','test_key-1')],'test_key-1'))
    def test_wrong_missing_empty_and_invalid_secret(self):
        h=[('x-telegram-bot-api-secret-token','test_key-1')]
        for secret in ('bad', '', None, 'bad space', 's'*257): self.assertFalse(verify(h,secret))
        self.assertFalse(verify([], 'test_key-1'))
    def test_duplicate_or_joined_headers_rejected(self):
        name='x-telegram-bot-api-secret-token'
        self.assertFalse(verify([(name,'test_key-1'),(name,'test_key-1')],'test_key-1'))
        self.assertFalse(verify([(name,'test_key-1,test_key-1')],'test_key-1'))
    def test_invalid_header_values(self):
        n='x-telegram-bot-api-secret-token'
        for v in (None,'', 'test_key-1 ', 'é', 'x'*257): self.assertFalse(verify([(n,v)],'test_key-1'))
    def test_invalid_input_fails_closed(self):
        for h in (None,[(None,'x')],[('bad',)],'bad'):
            self.assertFalse(verify(h,'test_key-1'))
    def test_irrelevant_headers_ignored(self):
        self.assertTrue(verify([('Content-Type','application/json'),('x-telegram-bot-api-secret-token','test_key-1')],'test_key-1'))
    def test_secret_is_not_enough_to_authorize_sender(self):
        # This module intentionally returns only transport validity; a verified
        # webhook still needs independent sender and destination authorization.
        self.assertTrue(verify([('x-telegram-bot-api-secret-token','abc')],'abc'))

if __name__ == '__main__': unittest.main()
