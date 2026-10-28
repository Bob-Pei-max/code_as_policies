"""Offline transport-contract tests. Mock replies are not language-model evidence."""
import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError
from urllib.request import Request

import run_planner
from src.model_provider import (MAX_RESPONSE_BYTES, ModelAPIError, QwenProgramProvider,
                                _NoRedirect, load_model_config)
from src.planning import build_messages

ROOT = Path(__file__).resolve().parents[1]
KEY = 'fake-secret-for-offline-tests-only'
BASE = 'https://example.test/compatible-mode/v1'
MODEL = 'qwen3.7-plus-2026-05-26'
PROGRAM = "put_first_on_second('blue block', 'yellow bowl')"


def completion(content=PROGRAM, finish='stop'):
    return {'id': 'mock-response', 'model': MODEL,
            'choices': [{'finish_reason': finish, 'message': {
                'role': 'assistant', 'content': content, 'reasoning_content': 'PRIVATE_REASONING'}}],
            'usage': {'prompt_tokens': 100, 'completion_tokens': 20, 'total_tokens': 120,
                      'untrusted_field': KEY}}


class MockTransport:
    def setUp(self):
        self.env = patch.dict(os.environ, {'DASHSCOPE_API_KEY': KEY}, clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)
        self.opener_patch = patch('src.model_provider.build_opener')
        self.opener = self.opener_patch.start().return_value
        self.addCleanup(self.opener_patch.stop)
        self.respond(completion())

    def respond(self, value):
        response = self.opener.open.return_value.__enter__.return_value
        response.status = 200
        response.read.return_value = value if isinstance(value, bytes) else json.dumps(value).encode()

    def provider(self, **overrides):
        return QwenProgramProvider(**{'base_url': BASE, 'model': MODEL, **overrides})


class ModelProviderTests(MockTransport, unittest.TestCase):
    def test_request_and_provenance_exclude_key_and_reasoning(self):
        provider = self.provider(thinking='disabled', max_completion_tokens=512)
        messages = build_messages('把蓝色积木放进黄色碗', ['blue block', 'yellow bowl'])
        reply = provider.generate(messages)
        request = self.opener.open.call_args.args[0]
        payload = json.loads(request.data)
        self.assertEqual(request.full_url, BASE + '/chat/completions')
        self.assertEqual(request.get_method(), 'POST')
        self.assertEqual(request.get_header('Authorization'), 'Bearer ' + KEY)
        self.assertEqual(payload['messages'], messages)
        self.assertEqual(payload['max_completion_tokens'], 512)
        self.assertFalse(payload['enable_thinking'])
        self.assertNotIn(KEY, request.data.decode())
        self.assertEqual(reply.text, PROGRAM)
        self.assertTrue(reply.model_api_used)
        self.assertEqual(reply.metadata['usage']['total_tokens'], 120)
        self.assertNotIn(KEY, json.dumps(reply.metadata))
        self.assertNotIn('PRIVATE_REASONING', json.dumps(reply.metadata))
        self.assertTrue(reply.metadata['reply_accepted'])

    def test_auto_thinking_omits_optional_parameter_and_full_endpoint_is_accepted(self):
        self.provider(base_url=BASE + '/chat/completions/').generate([])
        request = self.opener.open.call_args.args[0]
        self.assertEqual(request.full_url, BASE + '/chat/completions')
        self.assertNotIn('enable_thinking', json.loads(request.data))
        self.assertEqual(self.opener.open.call_args.kwargs['timeout'], 120)

    def test_missing_or_invalid_key_never_calls_transport(self):
        for key in ('', 'has\nnewline', '非ASCII'):
            with self.subTest(key=key), patch.dict(os.environ, {'DASHSCOPE_API_KEY': key}):
                provider = self.provider()
                with self.assertRaises(ModelAPIError):
                    provider.generate([])
                self.assertFalse(provider.metadata['api_call_attempted'])
        self.opener.open.assert_not_called()

    def test_invalid_endpoints_and_limits_fail_before_transport(self):
        cases = [{'base_url': value} for value in (
            'http://example.test/v1', 'https://user:secret@example.test/v1',
            BASE + '?api_key=secret', BASE + '#fragment', 'https://example.test:81/v1',
            'https://example.test\n/v1', 'https://example.test:bad/v1',
        )] + [{'timeout': float('nan')}, {'timeout': 0}, {'timeout': 301},
              {'max_completion_tokens': 0}, {'max_completion_tokens': 32769},
              {'thinking': 'other'}, {'model': ''}]
        for case in cases:
            with self.subTest(case=case), self.assertRaises(ModelAPIError):
                self.provider(**case)
        self.opener.open.assert_not_called()

    def test_redirect_handler_refuses_credential_forwarding(self):
        request = Request(BASE, data=b'{}', headers={'Authorization': 'Bearer ' + KEY})
        self.assertIsNone(_NoRedirect().redirect_request(
            request, None, 302, 'Found', {}, 'https://other.example/v1'))

    def test_http_errors_are_recorded_without_raw_body_or_retry(self):
        for status in (302, 400, 401, 403, 429, 500):
            with self.subTest(status=status):
                self.opener.open.reset_mock()
                self.opener.open.side_effect = HTTPError(BASE, status, KEY, {}, io.BytesIO(KEY.encode()))
                provider = self.provider()
                with self.assertRaises(ModelAPIError) as caught:
                    provider.generate([])
                self.assertNotIn(KEY, str(caught.exception))
                self.assertEqual(provider.metadata['http_status'], status)
                self.assertTrue(provider.metadata['api_call_attempted'])
                self.assertTrue(provider.metadata['response_received'])
                self.assertFalse(provider.metadata['reply_accepted'])
                self.opener.open.assert_called_once()

    def test_timeout_and_transport_errors_do_not_log_exception_details(self):
        for exc in (TimeoutError(KEY), URLError(KEY), OSError(KEY)):
            self.opener.open.side_effect = exc
            with self.subTest(kind=type(exc).__name__), self.assertRaises(ModelAPIError) as caught:
                self.provider().generate([])
            self.assertNotIn(KEY, str(caught.exception))

    def test_incomplete_and_malformed_responses_are_rejected(self):
        for value in (b'not JSON', [], {}, {'choices': [None]}, completion('', 'stop'),
                      completion(None), completion(PROGRAM, 'length'),
                      completion(PROGRAM, 'tool_calls'), completion(PROGRAM, None),
                      completion('x' * 16385), b'x' * (MAX_RESPONSE_BYTES + 1)):
            with self.subTest(value=str(value)[:40]):
                self.respond(value)
                provider = self.provider()
                with self.assertRaises(ModelAPIError):
                    provider.generate([])
                self.assertFalse(provider.metadata['reply_accepted'])

    def test_refusal_or_tools_are_not_accepted_even_with_stop(self):
        for field, value in [('refusal', 'refused'), ('tool_calls', [{'name': 'unsafe'}])]:
            response = completion()
            response['choices'][0]['message'][field] = value
            self.respond(response)
            with self.assertRaises(ModelAPIError):
                self.provider().generate([])

    def test_echoed_key_is_redacted_and_reply_is_rejected(self):
        response = completion('# ' + KEY)
        response['id'] = KEY
        self.respond(response)
        provider = self.provider()
        with self.assertRaises(ModelAPIError):
            provider.generate([])
        self.assertNotIn(KEY, provider.response_text)
        self.assertNotIn(KEY, json.dumps(provider.metadata))


class ModelEntryTests(MockTransport, unittest.TestCase):
    def cli(self, out, *extra):
        return ['--task', '把蓝色积木放进黄色碗', '--scene', str(ROOT / 'examples/planning/single.json'),
                '--model-api', '--output', str(out), *extra]

    def call(self, args):
        with contextlib.redirect_stdout(io.StringIO()):
            return run_planner.main(args)

    def test_model_compile_only_records_api_provenance_without_claiming_task_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / 'run'
            with patch('run_planner.subprocess.run') as worker:
                self.assertEqual(self.call(self.cli(out)), 0)
            worker.assert_not_called()
            report = json.loads((out / 'result.json').read_text(encoding='utf-8'))
            self.assertEqual(report['phase'], 'model_program_generation')
            self.assertTrue(report['model_api_used'])
            self.assertTrue(report['plan_validated'])
            self.assertIsNone(report['task_success'])
            self.assertIsNone(report['success'])
            payload = json.loads(self.opener.open.call_args.args[0].data)
            self.assertNotIn('goals', json.loads(payload['messages'][1]['content']))
            self.assertIn('把蓝色积木', payload['messages'][1]['content'])
            for path in out.iterdir():
                self.assertNotIn(KEY, path.read_text(encoding='utf-8'))

    def test_failed_or_unsafe_reply_never_starts_worker_and_retains_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            for index, response in enumerate((completion(PROGRAM, 'length'), completion('import os'))):
                self.respond(response)
                out = Path(tmp) / str(index)
                with patch('run_planner.subprocess.run') as worker:
                    self.assertEqual(self.call(self.cli(out, '--execute')), 1)
                worker.assert_not_called()
                report = json.loads((out / 'result.json').read_text(encoding='utf-8'))
                self.assertTrue(report['model_api_used'])
                self.assertFalse(report['plan_validated'])
                self.assertFalse(report['execution_completed'])
                self.assertTrue((out / 'response.txt').is_file())
                self.assertFalse((out / 'plan.json').exists())

    def test_missing_key_records_no_api_call(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {'DASHSCOPE_API_KEY': ''}):
            out = Path(tmp) / 'run'
            self.assertEqual(self.call(self.cli(out)), 1)
            report = json.loads((out / 'result.json').read_text(encoding='utf-8'))
            self.assertFalse(report['model_api_used'])
            self.assertFalse(report['provider']['api_call_attempted'])
            self.opener.open.assert_not_called()

    def test_cli_overrides_environment_which_overrides_config(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {
            'CAP_MODEL_BASE_URL': BASE, 'CAP_MODEL_NAME': 'env-model',
        }):
            self.assertEqual(self.call(self.cli(Path(tmp) / 'env')), 0)
            request = self.opener.open.call_args.args[0]
            self.assertEqual(request.full_url, BASE + '/chat/completions')
            self.assertEqual(json.loads(request.data)['model'], 'env-model')
            self.assertEqual(self.call(self.cli(Path(tmp) / 'cli', '--base-url', 'https://cli.test/v1',
                                                '--model', 'cli-model')), 0)
            request = self.opener.open.call_args.args[0]
            self.assertEqual(request.full_url, 'https://cli.test/v1/chat/completions')
            self.assertEqual(json.loads(request.data)['model'], 'cli-model')

    def test_source_flags_are_mutually_exclusive(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            self.call(self.cli(Path('unused'), '--program-file', 'unused.py'))
        self.opener.open.assert_not_called()

    def test_model_config_rejects_secret_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'config.json'
            path.write_text(json.dumps({'base_url': BASE, 'model': MODEL, 'api_key': KEY}))
            with self.assertRaises(ModelAPIError) as caught:
                load_model_config(path)
            self.assertNotIn(KEY, str(caught.exception))


if __name__ == '__main__':
    unittest.main()
