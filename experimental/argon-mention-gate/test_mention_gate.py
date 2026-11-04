import unittest
from mention_gate import directly_mentions_argon, dispatch_if_mentioned

class MentionGateTests(unittest.TestCase):
    def test_explicit(self):
        for s in ('@Argon سلام', 'درود @argon!', '@ARGON', '@آرگان، سلام', '@آرگون، سلام', 'با @آرگان صحبت کن'):
            with self.subTest(s=s): self.assertTrue(directly_mentions_argon(s))
    def test_other_messages_not_triggered(self):
        for s in ('سلام', '@Coca سلام', '@Notion2 test', '', None, 2):
            with self.subTest(s=s): self.assertFalse(directly_mentions_argon(s))
    def test_suffix_or_embedded_not_accepted(self):
        for s in ('@argon-task', '@Argon2', '@Argon_test', '@آرگان۲', '@آرگون۲', '@آرگون-task', '@آرگان\u200cکار', 'name@Argon', '@@Argon'):
            with self.subTest(s=s): self.assertFalse(directly_mentions_argon(s))
    def test_not_called_when_not_mentioned(self):
        calls=[]; self.assertFalse(dispatch_if_mentioned('@Coca hi',calls.append)); self.assertEqual(calls,[])
    def test_called_once_when_mentioned(self):
        calls=[]; self.assertTrue(dispatch_if_mentioned('@Argon hi',calls.append)); self.assertEqual(calls,['@Argon hi'])
    def test_task_disabled_even_in_sentence(self):
        calls=[]; self.assertFalse(dispatch_if_mentioned('stop @argon-task please',calls.append)); self.assertFalse(calls)
if __name__ == '__main__': unittest.main()
