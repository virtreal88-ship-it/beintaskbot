import os
import unittest
from unittest.mock import patch

from tenant_auth_api import _product_destination


class ConnectorProductContextTests(unittest.TestCase):
    def test_connect_destination_is_fixed_and_requires_the_configured_callback(self):
        with patch.dict(os.environ, {'CONNECT_SSO_REDIRECT_URI': 'https://connect.crm.pro.az/admin'}):
            self.assertEqual(_product_destination('connect'), 'https://connect.crm.pro.az/login?autostart=1')
        with patch.dict(os.environ, {'CONNECT_SSO_REDIRECT_URI': 'https://attacker.invalid/admin'}):
            self.assertEqual(_product_destination('connect'), '/connector/login')
        self.assertEqual(_product_destination('beintaskbot'), '/app')


if __name__ == '__main__':
    unittest.main()
