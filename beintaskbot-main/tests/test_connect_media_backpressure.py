"""Shared UI/AI transport admission with synthetic bytes; no external calls."""
import asyncio
import concurrent.futures
import threading
from unittest.mock import AsyncMock
import pytest
import tenant_connect_media as media
from tenant_linear_policy import TenantLinearError


def item(tenant='tenant-a', channel='channel-a', message='photo-a', version=1):
    return {'route':{'tenant_id':tenant,'channel_id':channel,'conversation_id':'dialog-a'},
            'message_id':message,'row':{'message_type':'image','raw':{'timestamp':version}}}


@pytest.fixture(autouse=True)
def isolated_process_state():
    media._media_budgets.clear(); media._media_flights.clear(); media._media_active=0
    yield
    assert media._media_active==0 and not media._media_flights


def test_ai_and_multiple_preview_waiters_share_exact_source_and_survive_waiter_abort(monkeypatch):
    async def scenario():
        started,release=asyncio.Event(),asyncio.Event()
        async def acquire(*_args,**_kwargs):
            started.set();await release.wait();return b'photo','image/png'
        transport=AsyncMock(side_effect=acquire);monkeypatch.setattr(media,'_download_media',transport)
        ai=asyncio.create_task(media.download_media(item()))
        await started.wait()
        tab_a=asyncio.create_task(media.download_media(item(),preview_documents=True))
        tab_b=asyncio.create_task(media.download_media(item()))
        await asyncio.sleep(0);tab_a.cancel()
        with pytest.raises(asyncio.CancelledError):await tab_a
        assert not ai.done() and not tab_b.done()
        release.set();assert await ai==await tab_b==(b'photo','image/png')
        assert transport.await_count==1
    asyncio.run(scenario())


def test_shared_two_slots_include_ai_and_other_tenants_channels(monkeypatch):
    async def scenario():
        release=asyncio.Event();entered=0
        async def acquire(*_args,**_kwargs):
            nonlocal entered
            entered+=1;await release.wait();return b'photo','image/png'
        transport=AsyncMock(side_effect=acquire);monkeypatch.setattr(media,'_download_media',transport)
        a=asyncio.create_task(media.download_media(item()))
        b=asyncio.create_task(media.download_media(item(channel='channel-b')))
        while entered<2:await asyncio.sleep(0)
        with pytest.raises(TenantLinearError) as error:await media.download_media(item(tenant='tenant-b'))
        assert error.value.status==429 and error.value.retry_after>=1
        assert transport.await_count==2
        release.set();await asyncio.gather(a,b)
    asyncio.run(scenario())


def test_429_cooldown_crosses_actors_channels_and_sources_but_not_tenants(monkeypatch):
    now=[1000.];monkeypatch.setattr(media.time,'monotonic',lambda:now[0])
    error=TenantLinearError('Synthetic backpressure',429);error.retry_after=17
    transport=AsyncMock(side_effect=[error,(b'photo','image/png'),(b'photo','image/png')])
    monkeypatch.setattr(media,'_download_media',transport)
    with pytest.raises(TenantLinearError):asyncio.run(media.download_media(item()))
    assert media.media_backpressure(item(channel='channel-b')['route'])==17
    with pytest.raises(TenantLinearError):asyncio.run(media.download_media(item(channel='channel-b',message='photo-b')))
    assert transport.await_count==1
    assert asyncio.run(media.download_media(item(tenant='tenant-b')))==(b'photo','image/png')
    now[0]+=18
    assert asyncio.run(media.download_media(item(message='photo-b')))==(b'photo','image/png')


def test_shared_tenant_twenty_per_minute_is_independent_of_channels(monkeypatch):
    now=[1000.];monkeypatch.setattr(media.time,'monotonic',lambda:now[0])
    transport=AsyncMock(return_value=(b'photo','image/png'));monkeypatch.setattr(media,'_download_media',transport)
    for n in range(20):asyncio.run(media.download_media(item(channel='channel-'+str(n),message='photo-'+str(n))))
    with pytest.raises(TenantLinearError) as error:asyncio.run(media.download_media(item(message='overflow')))
    assert error.value.status==429 and error.value.retry_after==60 and transport.await_count==20
    now[0]+=61;asyncio.run(media.download_media(item(message='later')))
    assert transport.await_count==21


def test_source_versions_and_tenants_never_share_bytes(monkeypatch):
    async def scenario():
        async def acquire(value,**_kwargs):
            await asyncio.sleep(0)
            return str(value['row']['raw']['timestamp']).encode(),'image/png'
        transport=AsyncMock(side_effect=acquire);monkeypatch.setattr(media,'_download_media',transport)
        assert await asyncio.gather(media.download_media(item()),media.download_media(item(version=2)))==[(b'1','image/png'),(b'2','image/png')]
        assert transport.await_count==2
        assert await media.download_media(item(tenant='tenant-b'))==(b'1','image/png')
        assert transport.await_count==3
    asyncio.run(scenario())


def test_ai_thread_event_loop_and_http_loop_share_a_single_flight(monkeypatch):
    started,release=threading.Event(),threading.Event()
    async def acquire(*_args,**_kwargs):
        started.set();await asyncio.to_thread(release.wait,2);return b'photo','image/png'
    transport=AsyncMock(side_effect=acquire);monkeypatch.setattr(media,'_download_media',transport)
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        ai=pool.submit(lambda:asyncio.run(media.download_media(item())))
        assert started.wait(1)
        async def request():
            waiting=asyncio.create_task(media.download_media(item()))
            await asyncio.sleep(0);release.set()
            return await waiting
        assert asyncio.run(request())==(b'photo','image/png')
        assert ai.result(timeout=2)==(b'photo','image/png')
        assert transport.await_count==1
