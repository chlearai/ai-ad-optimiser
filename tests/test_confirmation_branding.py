"""Password confirmation branding follows the activated account's product."""
import ast
import logging
import os
from pathlib import Path
import unittest
from unittest.mock import patch
from backend.services import onboarding_email

source=Path(__file__).resolve().parents[1]/'backend/routes/auth.py'
ns={'logger':logging.getLogger('test')}
for node in ast.parse(source.read_text(encoding='utf-8')).body:
    if isinstance(node,ast.FunctionDef) and node.name=='_password_confirmation_worker':
        exec(compile(ast.Module(body=[node],type_ignores=[]),str(source),'exec'),ns)

class ConfirmationBrandingTests(unittest.TestCase):
    @patch.object(onboarding_email,'send_platform_password_set_confirmation',return_value={'sent':True})
    @patch.object(onboarding_email,'send_adguard_password_set_confirmation',return_value={'sent':True})
    def test_main_platform_user_receives_platform_email(self,adguard,platform):
        ns['_password_confirmation_worker']('staff@example.test','Staff',False)
        platform.assert_called_once_with(recipient_email='staff@example.test',full_name='Staff',timeout=30)
        adguard.assert_not_called()
    @patch.object(onboarding_email,'send_platform_password_set_confirmation',return_value={'sent':True})
    @patch.object(onboarding_email,'send_adguard_password_set_confirmation',return_value={'sent':True})
    def test_adguard_subscriber_retains_product_confirmation(self,adguard,platform):
        ns['_password_confirmation_worker']('subscriber@example.test','Subscriber',True)
        adguard.assert_called_once()
        platform.assert_not_called()
    @patch.object(onboarding_email,'_send_raw_email',return_value={'sent':True})
    def test_platform_sender_body_and_destination(self,send):
        with patch.dict(os.environ,{'SMTP_SENDER_NAME':'AdGuard Notification','ADOPTIMA_PUBLIC_BASE_URL':'https://app.example.test'}):
            onboarding_email.send_platform_password_set_confirmation('staff@example.test','Staff <User>')
        args=send.call_args.args
        self.assertEqual(args[5],'ChlearSakhaaOps AI')
        self.assertIn('ChlearSakhaaOps AI',args[1])
        self.assertNotIn('AdGuard',args[2]+args[3])
        self.assertIn('https://app.example.test/',args[3])
        self.assertIn('Staff &lt;User&gt;',args[3])
if __name__=='__main__': unittest.main()
