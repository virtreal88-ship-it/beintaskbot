from tenant_policy import TenantPolicy

def test_catalog_uses_kommo_assignment_not_native_rights():
    profile={'tenant_id':'a','telegram_id':20,'role':'manager','active':True,'permissions':[], 'modules':{'deals':False},'workflow':{
        'pipelines':[{'pipeline_id':1,'name':'Mine'},{'pipeline_id':2,'name':'Other'}],
        'stages':[{'pipeline_id':1,'stage_id':11,'name':'Empty','sort_order':2},{'pipeline_id':1,'stage_id':10,'name':'New','sort_order':1},{'pipeline_id':2,'stage_id':21}],
        'policies':[{'policy_key':'members','value':{'20':{'pipeline_ids':['1']}}}]}}
    result=TenantPolicy(profile).deal_board()
    assert [p['pipeline_id'] for p in result]==[1]
    assert [s['stage_id'] for s in result[0]['stages']]==[10,11]
    profile['workflow']['policies'][0]['value']['20']['pipeline_ids']=[]
    assert TenantPolicy(profile).deal_board()==[]
