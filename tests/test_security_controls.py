import unittest
from security_controls import _valid_origin, rate_limit_status

class SecurityControlTests(unittest.TestCase):
    def test_https_origin_is_required(self):
        self.assertTrue(_valid_origin("https://nft-gift-scanner.pages.dev"))
        self.assertFalse(_valid_origin("http://nft-gift-scanner.pages.dev"))
        self.assertFalse(_valid_origin("https://nft-gift-scanner.pages.dev.evil.example"))

    def test_origin_with_path_is_rejected(self):
        self.assertFalse(_valid_origin("https://nft-gift-scanner.pages.dev/path"))

if __name__ == "__main__":
    unittest.main()
