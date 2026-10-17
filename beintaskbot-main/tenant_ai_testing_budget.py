"""Shared platform-token admission includes isolated virtual usage/reservations."""
TOKEN_USAGE_SQL = '''SELECT COALESCE(sum(total_tokens+reserved_tokens),0) +
 (SELECT COALESCE(sum(total_tokens+reserved_tokens),0) FROM saas_ai_test_turns
   WHERE tenant_id=%s::uuid AND created_at>=(now() AT TIME ZONE 'UTC')::date AT TIME ZONE 'UTC') AS tokens
 FROM saas_ai_agent_token_usage WHERE tenant_id=%s::uuid
   AND created_at>=(now() AT TIME ZONE 'UTC')::date AT TIME ZONE 'UTC'
'''
