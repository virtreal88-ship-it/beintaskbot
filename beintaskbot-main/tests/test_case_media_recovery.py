import copy
import pytest
from tenant_case_media_recovery import no_media_dispatch_proven


def attempt(kind='image'):
    suffix={'image':'vision','audio':'asr','video':'vision'}[kind]
    fragment={'status':'partial','extraction_id':'run','route':{'message_ids':['source']}}
    result={'id':'source','kind':kind,'status':'error','code':'media_unavailable',
            'failure_code':'media_processing_failed',
            'failure_stage':'media_validation' if kind=='audio' else 'visual_analysis'}
    reservations=[{'call_key':'extraction:run:'+suffix,'state':'released','actual_microusd':0}]
    if kind=='video':
        result['audio_status']='unavailable_or_error'
        reservations.append({'call_key':'extraction:run:asr','state':'released','actual_microusd':0})
    return fragment,{'id':'run','status':'completed','media_results':[result]},reservations


@pytest.mark.parametrize('kind',['image','audio','video'])
def test_only_exact_completed_failed_no_dispatch_receipts_are_eligible(kind):
    assert no_media_dispatch_proven(*attempt(kind))


@pytest.mark.parametrize('mutation',[
    'unknown_fragment','unknown_extraction','success','other_source','missing_reservation',
    'unknown_reservation','settled_reservation','charged_release','missing_failure_stage','video_audio_success'])
def test_recovery_fails_closed(mutation):
    kind='video' if mutation=='video_audio_success' else 'image'
    fragment,extraction,rows=copy.deepcopy(attempt(kind))
    if mutation=='unknown_fragment':fragment['status']='unknown'
    if mutation=='unknown_extraction':extraction['status']='unknown'
    if mutation=='success':extraction['media_results'][0]['status']='processed'
    if mutation=='other_source':extraction['media_results'][0]['id']='other'
    if mutation=='missing_reservation':rows.clear()
    if mutation=='unknown_reservation':rows[0]['state']='unknown'
    if mutation=='settled_reservation':rows[0]['state']='settled'
    if mutation=='charged_release':rows[0]['actual_microusd']=1
    if mutation=='missing_failure_stage':extraction['media_results'][0].pop('failure_stage')
    if mutation=='video_audio_success':extraction['media_results'][0]['audio_status']='processed'
    assert not no_media_dispatch_proven(fragment,extraction,rows)
