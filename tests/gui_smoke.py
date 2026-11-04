"""Run the authenticated desktop smoke test in an isolated PostgreSQL schema.

Usage: python -m tests.gui_smoke
"""
import os
import unittest


def main():
    os.environ['SAFETY_MONITOR_TEST_UI'] = '1'
    from tests.test_ui import DesktopTests
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(DesktopTests)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    raise SystemExit(0 if result.wasSuccessful() else 1)


if __name__ == '__main__':
    main()
