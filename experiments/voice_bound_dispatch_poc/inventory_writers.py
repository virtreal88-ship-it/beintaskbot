"""Conservative text-search starting point; never claims a complete fleet audit."""
from pathlib import Path
import re

ROOT=Path(__file__).resolve().parents[2]/'beintaskbot-main'
TABLES='''
saas_tenants saas_tenant_members saas_tenant_pipelines saas_tenant_pipeline_stages
saas_tenant_workflow_policies saas_tenant_integrations saas_ai_agent_settings
saas_ai_agents saas_ai_conversation_state saas_ai_group_coordination saas_ai_group_events
saas_connect_conversations saas_connect_channels saas_connect_identity_conflicts
saas_connect_identity_conflict_events saas_connect_direct_identities saas_connect_dialog_links
saas_connect_owner_identity_assertions saas_ai_dialogue_merges saas_crm_native_contact_conversations
saas_crm_native_contact_identities saas_crm_messages saas_operator_copilot_threads
saas_ai_knowledge_sources saas_ai_knowledge_chunks saas_knowledge_candidates
saas_knowledge_candidate_sources saas_instruction_lessons saas_media_evidence_receipts
saas_voice_test_grant saas_voice_test_hold saas_voice_test_session saas_ai_spend_reservations
'''.split()

def main():
    paths=[p for p in ROOT.rglob('*') if p.suffix in {'.py','.sql','.ts','.js'} and
           not any(x in {'tests','test','node_modules','__pycache__'} for x in p.parts)]
    for table in TABLES:
        expression=re.compile(r'\b(?:insert\s+into|update|delete\s+from|truncate(?:\s+table)?|merge\s+into)\s+(?:public\.)?'+re.escape(table)+r'\b',re.I)
        hits=[]
        for path in paths:
            try:source=path.read_text(encoding='utf-8')
            except (OSError,UnicodeError):continue
            for found in expression.finditer(source):
                hits.append(f'{path.relative_to(ROOT)}:{source.count(chr(10),0,found.start())+1}')
        print(table+'\t'+(', '.join(hits) if hits else '(none found by this pattern)'))

if __name__=='__main__':main()
