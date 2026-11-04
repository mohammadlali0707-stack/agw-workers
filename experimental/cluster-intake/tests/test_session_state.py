import unittest
from hq.session_state import classify_session

class SessionStateTest(unittest.TestCase):
    def setUp(self):
        self.session = {'object':'session', 'id':'session-1', 'agent_id':'agent-1', 'status':'queued'}
    def decide(self, obj=None, sid='session-1', aid='agent-1'):
        return classify_session(self.session if obj is None else obj, expected_session_id=sid, expected_agent_id=aid)
    def test_pending(self):
        for status in ('queued','in_progress'):
            with self.subTest(status=status):
                self.session['status']=status
                self.assertEqual(self.decide().state,'pending')
    def test_complete_not_evidence_of_success(self):
        self.session['status']='completed'
        self.assertEqual(self.decide().state,'completed_unverified')
    def test_stopped(self):
        for status in ('failed','canceled','terminated'):
            with self.subTest(status=status):
                self.session['status']=status
                self.assertEqual(self.decide().state,'stopped')
    def test_requires_action_waits_not_approve(self):
        self.session.update(status='requires_action',required_actions=[{'action_id':'a1','title':'Review', 'options':[{'id':'approve','label':'OK'},{'id':'reject','label':'No'}]}])
        self.assertEqual(self.decide().state,'await_human')
    def test_requires_action_without_actions(self):
        self.session['status']='requires_action'
        self.assertEqual(self.decide().state,'manual_review')
    def test_bad_options(self):
        self.session.update(status='requires_action',required_actions=[{'action_id':'a','options':[{'id':'other'}]}])
        self.assertEqual(self.decide().state,'manual_review')
    def test_missing_action_id(self):
        self.session.update(status='requires_action',required_actions=[{'options':[{'id':'approve'}]}])
        self.assertEqual(self.decide().state,'manual_review')
    def test_unexpected_actions(self):
        self.session['required_actions']=[{'action_id':'a'}]
        self.assertEqual(self.decide().state,'manual_review')
    def test_bad_actions_type(self):
        self.session['required_actions']=None
        self.assertEqual(self.decide().state,'manual_review')
    def test_invalid_status(self):
        for status in (None, [], 'new_status'):
            with self.subTest(status=status):
                self.session['status']=status
                self.assertEqual(self.decide().state,'manual_review')
    def test_invalid_object(self):
        self.session['object']='error'
        self.assertEqual(self.decide().state,'manual_review')
    def test_invalid_response(self):
        self.assertEqual(self.decide([]).state,'manual_review')
    def test_wrong_session_or_agent(self):
        self.assertEqual(self.decide(sid='different').state,'manual_review')
        self.assertEqual(self.decide(aid='different').state,'manual_review')
    def test_missing_expected_identity(self):
        self.assertEqual(self.decide(sid='').state,'manual_review')
        self.assertEqual(self.decide(aid=' ').state,'manual_review')
    def test_no_sensitive_details_in_decision(self):
        self.session['secret']='do not copy'
        self.session['status']='completed'
        self.assertNotIn('do not copy',repr(self.decide()))

if __name__=='__main__': unittest.main()
