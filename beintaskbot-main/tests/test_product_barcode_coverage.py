"""Private barcode projection must preserve the exact, fail-closed media proof."""
import copy
from unittest.mock import Mock

import pytest
from tenant_linear_policy import TenantLinearError
from tenant_media_coverage import MediaCoverage, CoverageError
from tenant_operator_copilot import _bounded_conversation
import tenant_episode_media as episode
import tenant_media_evidence as evidence
from test_episode_media import Authority, row
from test_product_barcode_evidence import TENANT, CHANNEL, REF, RAW, OBS, EAN, retained

PHONE='994501234567'


def project(*,reused=False,text=None,change=None):
    text=text or OBS+'\nContact phone: +'+PHONE+'.'
    photo=row('image',channel='connect:'+CHANNEL,external_id=REF['external_id'],raw=RAW)
    authority=Authority([photo]);authority.tenant=TENANT;authority.channel=CHANNEL
    ref=episode.reference(photo,CHANNEL,authority.lead)
    saved=retained(text)
    if change:change(saved)
    original=copy.deepcopy(saved)
    message={'text':''}
    if reused:
        receipt=evidence.apply_reusable(message,saved)
    else:
        receipt={'status':saved['status'],'code':saved['code'],'coverage':saved['coverage'],**saved['result']}
        for observed in receipt['observations']:
            message['text']+='\n[Untrusted visual observation; not a confirmed solution] '+observed['text']
    before_receipt=copy.deepcopy(receipt)
    episode.apply_episode_receipt(photo,message,receipt,authority=authority,ref=ref,saved=saved)
    assert saved==original and receipt==before_receipt
    assert photo['_episode_receipt'] is not receipt
    assert photo['_episode_receipt']['observations'] is not receipt['observations']
    return photo,authority,ref


def rendered(photo,authority,manual):
    manifest=MediaCoverage([photo],TENANT,CHANNEL,authority.lead,latest_request=manual)
    if manual:
        lines=_bounded_conversation([photo],audio_coverage=manifest.coverage,coverage_manifest=manifest)
    else:
        lines,_=episode.context_lines([photo],[photo],coverage=manifest)
    return manifest,lines


@pytest.mark.parametrize('reused',[False,True])
@pytest.mark.parametrize('manual',[False,True])
@pytest.mark.parametrize('same_phone',[False,True])
def test_full_receipt_keeps_only_eligible_barcode_and_seals(reused,manual,same_phone):
    photo,authority,_=project(reused=reused,text=OBS+'\nContact phone: +'+(EAN if same_phone else PHONE)+'.')
    manifest,lines=rendered(photo,authority,manual)
    manifest.seal(lines);manifest.check(lines)
    assert (EAN in str(lines)) is not same_phone
    assert PHONE not in str(lines)
    assert EAN not in str(photo['_episode_receipt']['observations'])
    with pytest.raises(CoverageError):manifest.check([{**lines[0],'text':'omitted evidence'}])


@pytest.mark.parametrize('reused',[False,True])
@pytest.mark.parametrize('manual',[False,True])
@pytest.mark.parametrize('damage',['partial','coverage','missing_component','context_limit','long_redaction'])
def test_projection_cannot_certify_incomplete_or_omitted_evidence(reused,manual,damage):
    def change(saved):
        if damage=='partial':saved['status']='partial'
        if damage=='coverage':saved['coverage']['complete']=False
        if damage=='missing_component':saved['result']['observations'].append({'text':'Required second visual fact.'})
    text=OBS+'\n'+('long incomplete observation '*190) if damage=='long_redaction' else None
    photo,authority,_=project(reused=reused,text=text,change=change)
    if damage=='missing_component':
        photo['_copilot_attachment_text']=photo['_copilot_attachment_text'].replace('Required second visual fact.','')
    if damage=='context_limit':photo['_copilot_attachment_text']='x'*24000+'\n'+photo['_copilot_attachment_text']
    manifest,lines=rendered(photo,authority,manual)
    with pytest.raises(CoverageError):manifest.seal(lines)


def test_projection_preserves_existing_truncation_flag():
    def change(saved):saved['result']['truncated']=True
    photo,authority,_=project(change=change)
    assert photo['_episode_receipt']['truncated'] is True
    manifest,lines=rendered(photo,authority,True)
    with pytest.raises(CoverageError):manifest.seal(lines)


@pytest.mark.parametrize('reused',[False,True])
@pytest.mark.parametrize('field',['source_hash','observation_hash'])
def test_mismatched_candidate_is_not_unmasked(reused,field):
    def change(saved):
        typed=saved['result']['product_barcode_candidate']
        if field=='source_hash':typed['source']['source_hash']='f'*64
        else:typed['artifact']['observation_hash']='f'*64
    photo,authority,_=project(reused=reused,change=change)
    manifest,lines=rendered(photo,authority,True)
    manifest.seal(lines)
    assert EAN not in str(lines) and PHONE not in str(lines)


@pytest.mark.parametrize('reused',[False,True])
def test_source_and_receipt_changes_still_invalidate_projected_proof(monkeypatch,reused):
    photo,authority,_=project(reused=reused)
    manifest,lines=rendered(photo,authority,True)
    manifest.seal(lines)
    monkeypatch.setattr('tenant_connect_dialog_equivalence.lead_members',Mock(return_value=[authority.lead]))
    monkeypatch.setattr(episode,'revalidate_sources',Mock(side_effect=TenantLinearError('Source changed',409)))
    with pytest.raises(CoverageError) as error:
        manifest.validate(Mock(),TENANT,CHANNEL,authority.lead,lines=lines)
    assert error.value.code=='media_source_changed'
    photo['_episode_receipt']['observations'][0]['text']='replacement'
    with pytest.raises(CoverageError) as error:manifest.check(lines)
    assert error.value.code=='media_manifest_changed'
