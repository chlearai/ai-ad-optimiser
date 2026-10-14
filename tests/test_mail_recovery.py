"""Existing Google configuration and SMTP recovery for invitation delivery."""
import os
import unittest
from unittest.mock import patch
from backend.services import gmail_api, onboarding_email

class MailRecoveryTests(unittest.TestCase):
    @patch('backend.services.config.load_config', return_value={'google_client_id':'saved-client','google_client_secret':'saved-secret'})
    def test_saved_google_client_is_used(self, config):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(gmail_api._get_client_config()['client_id'], 'saved-client')
            self.assertEqual(gmail_api._get_oauth_config('https://app.test/api/auth/gmail/callback')['redirect_uri'], 'https://app.test/api/auth/gmail/callback')
    def test_explicit_gmail_client_takes_precedence(self):
        with patch.dict(os.environ, {'GMAIL_CLIENT_ID':'gmail-client','GMAIL_CLIENT_SECRET':'gmail-secret'}, clear=True):
            self.assertEqual(gmail_api._get_client_config()['client_id'], 'gmail-client')
    @patch('backend.services.onboarding_email._send_via_smtp', return_value={'sent':True,'provider':'smtp'})
    @patch('backend.services.gmail_api.send_email_via_gmail_api', return_value={'sent':False,'error':'invalid_grant'})
    def test_failed_gmail_uses_existing_smtp(self, gmail, smtp):
        result=onboarding_email.send_onboarding_email('user@example.test','User','https://app.test/setup',refresh_token='saved-token',timeout=7)
        self.assertTrue(result['sent'])
        smtp.assert_called_once_with('user@example.test','User','https://app.test/setup',timeout=7)
    @patch('backend.services.onboarding_email._send_via_smtp')
    @patch('backend.services.gmail_api.send_email_via_gmail_api', return_value={'sent':True,'provider':'gmail_api'})
    def test_successful_gmail_is_not_duplicated(self, gmail, smtp):
        self.assertTrue(onboarding_email.send_onboarding_email('user@example.test','User','https://app.test/setup',refresh_token='saved-token')['sent'])
        smtp.assert_not_called()
    @patch('backend.services.onboarding_email._send_via_smtp', return_value={'sent':False,'error':'connection timed out'})
    @patch('backend.services.gmail_api.send_email_via_gmail_api', return_value={'sent':False,'error':'invalid_grant'})
    def test_both_provider_errors_are_reported(self, gmail, smtp):
        result=onboarding_email.send_onboarding_email('user@example.test','User','https://app.test/setup',refresh_token='saved-token')
        self.assertFalse(result['sent'])
        self.assertIn('invalid_grant',result['error'])
        self.assertIn('connection timed out',result['error'])
if __name__=='__main__': unittest.main()
