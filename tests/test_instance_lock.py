"""同一本账不能同时写入；锁释放后可以再次打开。"""

from pathlib import Path
import tempfile
import unittest

from ledger_core import LedgerError
from travel_ledger import InstanceLock


class LockTests(unittest.TestCase):
    def test_exclusive_lock_and_release(self):
        with tempfile.TemporaryDirectory() as directory:
            data_path = Path(directory) / "旅行账本.json"
            with InstanceLock(data_path):
                with self.assertRaises(LedgerError):
                    with InstanceLock(data_path):
                        pass
            with InstanceLock(data_path):
                self.assertTrue(data_path.with_suffix(".json.lock").exists())


if __name__ == "__main__":
    unittest.main()
