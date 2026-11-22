"""Testes offline da fila auxiliar JPEG: catálogo e rede reais nunca acessados."""
import importlib.util
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
FILE = ROOT / "services/arena-adapter/thumbnail_queue.py"
spec = importlib.util.spec_from_file_location("thumbnail_queue", FILE)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class QueueTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.queue = module.ThumbnailQueue(
            self.root / "queue.sqlite", self.root / "catalogo.sqlite"
        )

    def test_enqueue_idempotent(self):
        self.assertEqual(self.queue.enqueue(["quadra01.0001"], now=100), 1)
        self.assertEqual(self.queue.enqueue(["quadra01.0001"], now=101), 0)
        self.assertEqual(self.queue.counts(), {"pending": 1})

    def test_restart_and_complete(self):
        self.queue.enqueue(["quadra01.0001"], now=100)
        self.assertEqual(self.queue.claim(now=101), ["quadra01.0001"])
        other = module.ThumbnailQueue(self.root / "queue.sqlite")
        self.assertEqual(other.claim(now=102), [])
        other.complete("quadra01.0001", now=105)
        self.assertEqual(self.queue.counts(), {"synced": 1})
        self.assertEqual(self.queue.claim(now=10000), [])

    def test_expired_lease_recovers(self):
        self.queue.enqueue(["quadra01.0001"], now=100)
        self.queue.claim(now=101, lease_s=60)
        self.assertEqual(self.queue.claim(now=160, lease_s=60), [])
        self.assertEqual(self.queue.claim(now=161, lease_s=60), ["quadra01.0001"])

    def test_network_retry_backoff(self):
        self.queue.enqueue(["quadra01.0001"], now=100)
        self.queue.claim(now=101)
        self.assertEqual(self.queue.retry("quadra01.0001", kind="network", now=102), 132)
        self.assertEqual(self.queue.claim(now=131), [])
        self.assertEqual(self.queue.claim(now=132), ["quadra01.0001"])
        self.assertEqual(self.queue.retry("quadra01.0001", now=133), 193)

    def test_permanent_failure(self):
        self.queue.enqueue(["quadra01.0001"], now=100)
        self.queue.claim(now=101)
        self.assertIsNone(self.queue.retry("quadra01.0001", kind="invalid", now=102, permanent=True))
        self.assertEqual(self.queue.counts(), {"failed": 1})
        self.assertEqual(self.queue.claim(now=999999), [])

    def test_queue_not_catalog(self):
        same = self.root / "catalogo.sqlite"
        with self.assertRaises(ValueError):
            module.ThumbnailQueue(same, same)

    def test_invalid_limit(self):
        with self.assertRaises(ValueError):
            self.queue.claim(limit=0)
        with self.assertRaises(ValueError):
            self.queue.claim(lease_s=0)

    def test_no_complete_without_claim(self):
        self.queue.enqueue(["quadra01.0001"], now=100)
        with self.assertRaises(ValueError):
            self.queue.complete("quadra01.0001")


if __name__ == "__main__":
    unittest.main()
