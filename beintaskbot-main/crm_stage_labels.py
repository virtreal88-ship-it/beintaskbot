"""Azerbaijani display labels; provider stages and IDs are never modified."""
TERMINAL_STAGE_LABELS = {142:'Uğurla tamamlandı',143:'İmtina olundu'}


def stage_display_name(status_id: object, original: object = '') -> str:
    try:
        status = int(status_id)
    except (TypeError,ValueError):
        status = 0
    return TERMINAL_STAGE_LABELS.get(status,str(original or ''))
