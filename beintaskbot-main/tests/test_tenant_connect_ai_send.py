import ast
import unittest
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load_send_functions():
    tree = ast.parse((ROOT / 'tenant_connect_transport.py').read_text(encoding='utf-8-sig'))
    functions = [node for node in tree.body if isinstance(node, ast.FunctionDef)
                 and node.name in {'send', 'send_ai_reply'}]
    namespace = {'uuid': uuid, 'TenantPlatformError': RuntimeError}
    exec(compile(ast.Module(body=functions, type_ignores=[]), 'tenant_connect_transport.py', 'exec'), namespace)
    return namespace['send_ai_reply']


send_ai_reply = load_send_functions()


class ConnectAiSendTests(unittest.TestCase):
    def test_ai_reply_uses_run_uuid_as_connect_client_message_id(self):
        run_id = uuid.uuid4()
        route = {'api_key': 'test-key', 'channel_id': str(uuid.uuid4()),
                 'conversation_id': 'test-contact@s.whatsapp.net'}
        captured = {}

        def request(key, method, path, payload):
            captured.update(key=key, method=method, path=path, payload=payload)
            return {'id': 'message-accepted'}

        namespace = send_ai_reply.__globals__
        namespace['_request'] = request
        result = send_ai_reply(route, 'test draft', run_id)

        self.assertEqual(result, 'message-accepted')
        self.assertEqual(captured['payload']['clientMessageId'], str(run_id))
        self.assertEqual(captured['payload']['type'], 'text')
        self.assertEqual(captured['path'], '/v1/channels/' + route['channel_id'] + '/messages')

    def test_ai_reply_rejects_non_uuid_run_id_before_connect_request(self):
        route = {'api_key': 'test-key', 'channel_id': str(uuid.uuid4()),
                 'conversation_id': 'test-contact@s.whatsapp.net'}

        namespace = send_ai_reply.__globals__
        request_called = []
        namespace['_request'] = lambda *args: request_called.append(args)
        with self.assertRaises(RuntimeError):
            send_ai_reply(route, 'test draft', 'ai-not-a-uuid')
        self.assertEqual(request_called, [])


if __name__ == '__main__':
    unittest.main()
