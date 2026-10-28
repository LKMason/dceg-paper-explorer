import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from audit_staff import compatible_given, identity


class StaffIdentityLeads(unittest.TestCase):
    def test_publishing_spacing_and_middle_initials(self):
        self.assertTrue(compatible_given('Hae Gin', 'Haegin'))
        self.assertTrue(compatible_given('Wen Yi', 'Wen-Yi'))
        self.assertTrue(compatible_given('Jonas S.', 'Jonas S'))
        self.assertEqual(identity('Anyaso Samuel', 'Samuel'),
                         identity('Anyaso-Samuel', 'Samuel'))

    def test_full_given_name_conflicts_do_not_match(self):
        self.assertFalse(compatible_given('Wei', 'Wei-Shau'))
        self.assertFalse(compatible_given('Thi Van Trinh', 'Thi-Ngoc'))
        self.assertFalse(compatible_given('David A.', 'David Roy'))
        self.assertFalse(compatible_given('', 'Jonas'))
        self.assertFalse(compatible_given('', ''))


if __name__ == '__main__':
    unittest.main()
