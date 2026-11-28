import os
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

import continuous_telegram_monitor as monitor


class ObserverTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(':memory:')
        self.addCleanup(self.db.close)
        self.time = 0
        self.sent = []

    def observer(self, send=None):
        return monitor.Observer(self.db, send or self.sent.append,
                                clock=lambda: self.time)

    def feed(self, observer, samples):
        for self.time, success in samples:
            observer.sample(success)

    def test_short_failure_only_long_outage_down_once_then_recovers_once(self):
        m = self.observer()
        self.feed(m, [(0, True), (30, False), (60, False), (90, True)])
        self.assertEqual(self.sent, [])
        self.feed(m, [(120, False), (150, False), (180, False), (210, False)])
        self.assertEqual(self.sent, ['down'])
        m = self.observer()  # New process reads durable state.
        self.feed(m, [(240, False), (270, False), (300, False), (330, True), (360, True)])
        self.assertEqual(self.sent, ['down', 'up'])

    def test_not_replayed_when_telegram_outcome_is_unknown(self):
        def send(state):
            self.sent.append(state)
            raise TimeoutError('sensitive information')
        m = self.observer(send)
        self.feed(m, [(0, False), (30, False), (60, False)])
        m = self.observer()
        self.feed(m, [(90, False), (120, False), (150, False), (180, True)])
        self.assertEqual(self.sent, ['down'])
        self.feed(m, [(210, False), (240, False), (270, False), (300, True)])
        self.assertEqual(self.sent, ['down', 'down', 'up'])

    def test_host_sleep_is_not_evidence_of_continuous_failure(self):
        m = self.observer()
        self.feed(m, [(0, False), (55, False), (85, False)])
        self.assertEqual(self.sent, [])
        self.feed(m, [(115, False)])
        self.assertEqual(self.sent, ['down'])

    def test_group_destination_never_receives_alert(self):
        with patch.dict(os.environ, {'CRM_CHAT_ID':'111'}), patch.object(
            monitor.telegram_monitor,'telegram',return_value={'type':'group','id':111}
        ) as api:
            with self.assertRaises(ValueError):
                monitor.send("down")
            api.assert_called_once_with('getChat',{})

    def test_main_enters_loop_with_mocked_private_telegram_and_public_probe(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root/'telegram-chat-id').write_text('111')
            (root/'telegram-bot-token').write_text('1:dummy-not-live')
            def sleep(seconds):
                self.assertEqual(seconds, 30)
                self.time += seconds

            def request(method, params):
                if method == 'getChat':
                    return {'type':'private','id':111}
                self.sent.append(params['text'])
                return {'message_id':1}

            with patch.dict(os.environ, {'CREDENTIALS_DIRECTORY':tmp, 'STATE_DIRECTORY':tmp}), patch.object(
                monitor.telegram_monitor, 'telegram', side_effect=request
            ):
                samples = iter([False,False,False,True])
                with self.assertRaises(StopIteration):
                    monitor.main(probe=lambda: next(samples), clock=lambda: self.time, sleep=sleep)
            self.assertEqual(len(self.sent), 2)
            self.assertIn('CRM недоступна.', self.sent[0])
            self.assertIn('CRM снова доступна.', self.sent[1])
            self.assertTrue((root/'monitor.sqlite').exists())


if __name__ == '__main__':
    unittest.main()
