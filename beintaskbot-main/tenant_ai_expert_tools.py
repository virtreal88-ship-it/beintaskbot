"""Read-only diagnostic registry. No production BeinSystems adapter is installed.

No URL, SQL, shell, credentials or caller-provided executable callbacks are accepted.
Mock fixtures are a separate function never reachable from the runtime executor.
"""
from tenant_ai_expert_policy import TOOLS

def diagnose(tool, *, tenant_id, channel_id, conversation_id):
    if tool not in TOOLS: raise ValueError('tool_not_allowed')
    return {'tool':tool,'status':'integration_unavailable','read_only':True,'evidence':[]}

def mock_diagnose(tool, fixture):
    if tool not in TOOLS: raise ValueError('tool_not_allowed')
    return {'tool':tool,'status':'mock','read_only':True,'evidence':[str(fixture)[:500]],'simulation':True}
