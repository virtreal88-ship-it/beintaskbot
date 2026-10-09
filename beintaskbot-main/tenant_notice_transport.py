"""One reserved delivery attempt. No database, logger, credentials or retry loop."""
import asyncio
from dataclasses import dataclass
from typing import Callable, Literal, Protocol


class TelegramMessage(Protocol):
    message_id: int


class TelegramBot(Protocol):
    async def send_message(self, **kwargs: object) -> TelegramMessage: ...


@dataclass(frozen=True)
class DeliveryResult:
    status: Literal['delivered', 'unknown', 'expired']
    message_id: int | None = None


async def attempt(item: dict, channel: str, bot: TelegramBot, *,
                  text: Callable[[dict], str], parts: Callable[[str], list[str]],
                  push_sender: Callable[[dict, str, dict], None] | None = None,
                  private_key: str = '', claims: dict | None = None) -> DeliveryResult:
    """Caller must have claimed/checkpointed the item and verified current rights.

    Provider acceptance is not proof of reading. CancelledError propagates,
    leaving the durable sending checkpoint to the existing stale cleanup.
    """
    if channel not in {'telegram', 'push'} or not item.get('send'):
        raise ValueError('Delivery must be an authorized claimed attempt')
    message_id = None
    try:
        if channel == 'telegram':
            for part in parts(text(item)):
                sent = await bot.send_message(chat_id=item['recipient_id'], text=part,
                                              parse_mode=None, disable_web_page_preview=True)
                message_id = sent.message_id
        else:
            if push_sender is None or not private_key:
                raise ValueError('Push transport is not configured')
            await asyncio.to_thread(push_sender, item, private_key, claims if claims is not None else {})
    except Exception as error:
        code = getattr(getattr(error, 'response', None), 'status_code', None)
        status = 'expired' if channel == 'push' and code in {404, 410} else 'unknown'
        return DeliveryResult(status, message_id)
    return DeliveryResult('delivered', message_id)
