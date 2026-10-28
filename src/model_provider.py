"""Qwen via Model Studio's HTTPS Chat Completions API (standard library only)."""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
import re
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from src.planning import MAX_SOURCE_BYTES, ProgramReply

MAX_RESPONSE_BYTES = 1024 * 1024


class ModelAPIError(ValueError):
    """A public error that never embeds response bodies, headers or credentials."""


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Never forward the bearer credential to a redirect destination.
        return None


def load_model_config(path: Path) -> dict:
    config = json.loads(path.read_text(encoding='utf-8-sig'))
    if not isinstance(config, dict) or set(config) != {'base_url', 'model'}:
        raise ModelAPIError('Model config must contain only base_url and model; use an environment variable for the key')
    return config


def _endpoint(base_url: str) -> str:
    try:
        if not isinstance(base_url, str) or any(c.isspace() for c in base_url):
            raise ValueError
        parts = urlsplit(base_url)
        if (parts.scheme != 'https' or not parts.hostname or parts.username is not None
                or parts.password is not None or parts.query or parts.fragment
                or parts.port not in (None, 443) or '\\' in base_url):
            raise ValueError
        path = parts.path.rstrip('/')
        if not path.endswith('/chat/completions'):
            path += '/chat/completions'
        return urlunsplit(('https', parts.netloc, path, '', ''))
    except (ValueError, TypeError):
        raise ModelAPIError('Use an HTTPS base URL on port 443 without credentials, query or fragment') from None


class QwenProgramProvider:
    """One non-streaming request; no retry and no execution of returned Python."""

    def __init__(self, *, base_url: str, model: str, api_key_env='DASHSCOPE_API_KEY',
                 timeout=120.0, max_completion_tokens=4096, thinking='auto'):
        self.endpoint = _endpoint(base_url)
        if not isinstance(model, str) or not re.fullmatch(r'[A-Za-z0-9._/-]{1,128}', model):
            raise ModelAPIError('Model must be a nonempty model identifier')
        if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', api_key_env):
            raise ModelAPIError('Invalid API key environment variable name')
        if not math.isfinite(timeout) or not 0 < timeout <= 300:
            raise ModelAPIError('request-timeout must be greater than 0 and at most 300 seconds')
        if type(max_completion_tokens) is not int or not 1 <= max_completion_tokens <= 32768:
            raise ModelAPIError('max-completion-tokens must be between 1 and 32768')
        if thinking not in ('auto', 'enabled', 'disabled'):
            raise ModelAPIError('thinking must be auto, enabled or disabled')
        self.api_key_env = api_key_env
        self.timeout = timeout
        self.response_text = None
        self.metadata = {
            'provider': 'aliyun_qwen_chat_completions', 'endpoint': self.endpoint,
            'model': model, 'api_key_env': api_key_env, 'api_call_attempted': False,
            'response_received': False, 'reply_accepted': False,
            'request_timeout_seconds': timeout, 'max_completion_tokens': max_completion_tokens,
            'thinking': thinking, 'temperature': 0, 'stream': False, 'retries': 0,
        }

    def generate(self, messages: list[dict[str, str]]) -> ProgramReply:
        key = os.environ.get(self.api_key_env, '').strip()
        if not key:
            raise ModelAPIError(f'Missing local environment variable: {self.api_key_env}')
        if any(ord(c) < 33 or ord(c) > 126 for c in key):
            raise ModelAPIError('API key must contain only visible ASCII characters')
        payload = {
            'model': self.metadata['model'], 'messages': messages, 'stream': False,
            'temperature': self.metadata['temperature'],
            'max_completion_tokens': self.metadata['max_completion_tokens'],
        }
        if self.metadata['thinking'] != 'auto':
            payload['enable_thinking'] = self.metadata['thinking'] == 'enabled'
        data = json.dumps(payload, ensure_ascii=False).encode('utf-8')
        if key.encode('utf-8') in data or key in self.endpoint:
            raise ModelAPIError('Credential found in request content or URL; request refused')
        request = Request(self.endpoint, data=data, method='POST', headers={
            'Authorization': f'Bearer {key}', 'Content-Type': 'application/json',
            'Accept': 'application/json',
        })
        try:
            opener = build_opener(_NoRedirect())
            self.metadata['api_call_attempted'] = True
            with opener.open(request, timeout=self.timeout) as response:
                self.metadata['http_status'] = response.status
                self.metadata['response_received'] = True
                raw = response.read(MAX_RESPONSE_BYTES + 1)
            if len(raw) > MAX_RESPONSE_BYTES:
                raise ModelAPIError('API response exceeds the 1 MiB limit')
            result = json.loads(raw)
            if not isinstance(result, dict):
                raise ModelAPIError('API response must be a JSON object')
            # Whitelist provenance fields; do not persist reasoning or arbitrary API metadata.
            for field in ('id', 'model'):
                value = result.get(field)
                if isinstance(value, str):
                    self.metadata['response_' + field] = value.replace(key, '[REDACTED]')[:256]
            usage = result.get('usage')
            if isinstance(usage, dict):
                self.metadata['usage'] = {name: usage[name] for name in (
                    'prompt_tokens', 'completion_tokens', 'total_tokens'
                ) if type(usage.get(name)) is int and usage[name] >= 0}
            choices = result.get('choices')
            if not isinstance(choices, list) or len(choices) != 1 or not isinstance(choices[0], dict):
                raise ModelAPIError('API response must contain exactly one choice')
            choice = choices[0]
            message = choice.get('message')
            if not isinstance(message, dict) or message.get('role') != 'assistant':
                raise ModelAPIError('API choice has no assistant message')
            content = message.get('content')
            if isinstance(content, str):
                if len(content.encode('utf-8')) > MAX_SOURCE_BYTES:
                    raise ModelAPIError('Model program exceeds the 16 KiB source limit')
                self.response_text = content.replace(key, '[REDACTED]')
                if key in content:
                    raise ModelAPIError('Credential found in API response; reply refused and redacted')
            finish = choice.get('finish_reason')
            self.metadata['finish_reason'] = finish if finish in (
                'stop', 'length', 'tool_calls', 'content_filter', 'function_call'
            ) else 'unknown'
            if finish != 'stop':
                raise ModelAPIError('API reply did not finish normally; incomplete or rejected code is not compiled')
            if message.get('refusal') or message.get('tool_calls') or message.get('function_call'):
                raise ModelAPIError('Expected program text, received a refusal or tool call')
            if not isinstance(content, str) or not content.strip():
                raise ModelAPIError('API reply contains no program text')
            self.metadata['reply_accepted'] = True
            return ProgramReply(content, True, dict(self.metadata))
        except HTTPError as exc:
            self.metadata.update(http_status=exc.code, response_received=True)
            exc.close()
            hints = {401: 'check key and region', 403: 'check model and workspace permissions',
                     429: 'check quota or rate limit', 400: 'check model and request parameters',
                     404: 'check endpoint and model identifier'}
            hint = hints.get(exc.code, 'request failed; no automatic retry')
            raise ModelAPIError(f'Model API HTTP {exc.code}: {hint}') from None
        except (TimeoutError, URLError):
            raise ModelAPIError('Model API connection or timeout failure; no automatic retry') from None
        except (ValueError, UnicodeError) as exc:
            if isinstance(exc, ModelAPIError):
                raise
            raise ModelAPIError('Model API returned invalid JSON or text') from None
        except Exception:
            # Transport exception messages may contain headers or proxy credentials.
            raise ModelAPIError('Model API transport or response failure; no automatic retry') from None
