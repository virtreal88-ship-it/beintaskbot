"""Bounded Realtime adapter for the owner-only analytics read registry."""
import asyncio
import json

from tenant_ai_business_analytics import _TOOLS, definitions, execute
from tenant_linear_policy import TenantLinearError


_VOICE_TOOLS = frozenset({
    'list_analytics_sources', 'get_company_risks', 'search_conversations',
    'get_conversation_timeline', 'search_support_cases', 'get_support_case_detail',
    'search_tasks', 'get_task_timeline', 'search_knowledge',
})
MAX_ARGUMENT_BYTES = 8192
MAX_RESULT_BYTES = 24576
_SAFE_FAILURES = {
    400: 'invalid_request',
    404: 'not_found',
    429: 'source_busy',
    503: 'source_unavailable',
}


def realtime_definitions():
    """Translate the existing read-only registry to Realtime function tools."""
    result = []
    for tool in definitions():
        name = tool.get('name')
        schema = tool.get('inputSchema')
        description = tool.get('description')
        if name not in _VOICE_TOOLS:
            continue
        if (name not in _TOOLS or not isinstance(schema, dict) or
                not isinstance(description, str) or not description):
            raise ValueError('analytics_tool_definition_invalid')
        result.append({
            'type': 'function',
            'name': name,
            'description': description,
            'parameters': schema,
        })
    if {item['name'] for item in result} != set(_VOICE_TOOLS):
        raise ValueError('analytics_tool_registry_mismatch')
    return result


class AnalyticsToolExecutor:
    """Execute only registered read tools with live voice and owner checks."""
    def __init__(self, authority):
        if (authority is None or not callable(getattr(authority, 'check', None)) or
                not isinstance(getattr(authority, 'profile', None), dict) or
                not isinstance(getattr(authority, 'session_scope', None), tuple)):
            raise ValueError('analytics_voice_authority_required')
        self.authority = authority
        self.names = _VOICE_TOOLS
        self.tools = realtime_definitions()

    async def __call__(self, name, arguments):
        if name not in self.names or not isinstance(arguments, dict):
            raise TenantLinearError('Unknown analytics tool.', 404)
        encoded = json.dumps(arguments, ensure_ascii=False, separators=(',', ':'), allow_nan=False)
        if len(encoded.encode('utf-8')) > MAX_ARGUMENT_BYTES:
            raise TenantLinearError('Invalid analytics arguments.', 400)
        scope = self.authority.session_scope
        await self.authority.check(scope, 'analytics_tool')
        try:
            result = await asyncio.to_thread(execute, self.authority.profile, name, arguments)
        except TenantLinearError as error:
            if error.status in {403, 409}:
                raise
            result = {'read_only': True, 'available': False,
                      'reason': _SAFE_FAILURES.get(error.status, 'source_unavailable')}
        await self.authority.check(scope, 'analytics_tool_result')
        output = json.dumps({'read_only': True, 'result': result}, ensure_ascii=False,
                             separators=(',', ':'), allow_nan=False)
        if len(output.encode('utf-8')) > MAX_RESULT_BYTES:
            output = json.dumps({'read_only': True, 'available': False,
                                 'reason': 'result_too_large'}, separators=(',', ':'))
        return output
