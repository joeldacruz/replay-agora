"""Testes do processador em lote sem rede, câmera, MP4 ou catálogo real."""
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parents[1] / "services/arena-adapter"


def module(filename, name):
    spec = importlib.util.spec_from_file_location(name, ROOT / filename)
    obj = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(obj)
    return obj


queue_module = module("thumbnail_queue.py", "thumbnail_queue_batch")
batch = module("thumbnail_batch.py", "thumbnail_batch")


class ThumbnailBatchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.queue = queue_module.ThumbnailQueue(
            self.root / "queue.sqlite", self.root / "catalogo.sqlite"
        )
        self.records = [
            {"local_id": f"quadra01.abcdef{i:03d}"} for i in range(5)
        ]
        self.replays = {r["local_id"]: r for r in self.records}
        self.client = Mock()
        self.extract = Mock()

    def cycle(self, sender, now=100):
        return batch.process_batch(
            self.queue,
            self.records,
            self.replays.get,
            self.client,
            self.extract,
            (self.root,),
            sender,
            now=now,
        )

    def test_only_two_uploads_per_cycle(self):
        sender = Mock(return_value="created")
        stats = self.cycle(sender)
        self.assertEqual(sender.call_count, 2)
        self.assertEqual(stats["synced"], 2)
        self.assertEqual(self.queue.counts(), {"pending": 3, "synced": 2})

    def test_following_cycles_do_not_resend_synced(self):
        sent = []
        def sender(_client, replay, _extract, _roots):
            sent.append(replay["local_id"])
            return "exists"
        self.cycle(sender, now=100)
        self.cycle(sender, now=101)
        self.cycle(sender, now=102)
        self.assertEqual(len(sent), 5)
        self.assertEqual(len(set(sent)), 5)
        self.assertEqual(self.queue.counts(), {"synced": 5})

    def test_transient_error_is_backed_off(self):
        sender = Mock(side_effect=OSError("temporario"))
        stats = self.cycle(sender, now=100)
        self.assertEqual(stats["retried"], 2)
        self.assertEqual(self.queue.counts(), {"pending": 5})
        next_call = Mock(return_value="created")
        stats2 = self.cycle(next_call, now=101)
        self.assertEqual(stats2["synced"], 2)
        self.assertEqual(next_call.call_count, 2)

    def test_missing_record_is_not_uploaded(self):
        first = self.records[0]["local_id"]
        del self.replays[first]
        sender = Mock(return_value="created")
        stats = self.cycle(sender, now=100)
        self.assertEqual(stats["retried"], 1)
        self.assertEqual(sender.call_count, 1)

    def test_restarts_dont_repeat_sent(self):
        sender = Mock(return_value="created")
        self.cycle(sender, now=100)
        other = queue_module.ThumbnailQueue(self.root / "queue.sqlite")
        stats = batch.process_batch(
            other, self.records, self.replays.get, self.client,
            self.extract, (self.root,), sender, now=101,
        )
        self.assertEqual(stats["synced"], 2)
        self.assertEqual(sender.call_count, 4)

    def test_cap_validation(self):
        with self.assertRaises(ValueError):
            batch.process_batch(
                self.queue, [], self.replays.get, self.client, self.extract,
                (self.root,), Mock(), max_uploads=3,
            )

    def test_unknown_server_reply_is_retryable(self):
        sender = Mock(return_value="unexpected")
        result = self.cycle(sender, now=100)
        self.assertEqual(result["retried"], 2)
        self.assertEqual(self.queue.counts(), {"pending": 5})


if __name__ == "__main__":
    unittest.main()
