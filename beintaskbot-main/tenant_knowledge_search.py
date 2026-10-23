"""Local, bounded multilingual query expansion for Knowledge Sources.

This is deliberately a retrieval aid, not an embedding provider.  It keeps
customer text in PostgreSQL and gives RU/AZ/EN variants of common support
terms to the existing lexical index.  A later opted-in embedding provider can
be added as a second scorer without changing access rules or the schema.
"""
import re


# Terms are intentionally small and domain-oriented.  They improve a genuine
# cross-language miss without pretending that a paid semantic model ran.
_GROUPS = (
    ('anbar', 'anbarlar', 'warehouse', 'warehouses', 'склад', 'склады'),
    ('yerdəyişmə', 'yerdeyisme', 'transfer', 'move', 'перемещение', 'перевести', 'перевод'),
    ('məhsul', 'mal', 'product', 'item', 'товар', 'товары'),
    ('qaytarma', 'return', 'refund', 'возврат', 'вернуть'),
    ('çatdırılma', 'delivery', 'shipping', 'доставка'),
    ('ölçü', 'размер', 'size'),
    ('sifariş', 'order', 'заказ'),
)


def terms(value, limit=24):
    """Return normalized original and aligned multilingual terms.

    The output is capped so the fallback cannot turn a short operator query
    into an unbounded database search.
    """
    words = re.findall(r"[\wəƏöÖüÜğĞşŞçıİ-]+", str(value or '').casefold())[:12]
    ordered = list(dict.fromkeys(words))
    seen = set(ordered)
    for group in _GROUPS:
        normalized = {part.casefold() for part in group}
        # `simple` FTS deliberately does not stem Russian or Azerbaijani.
        # A conservative four-character prefix bridge covers ordinary forms
        # such as «склада» while keeping this fallback local and bounded.
        if seen & normalized or any(word[:4] == term[:4] for word in seen for term in normalized if len(word) >= 4 and len(term) >= 4):
            for term in group:
                term = term.casefold()
                if term not in seen:
                    seen.add(term)
                    ordered.append(term)
    return ordered[:limit]


def tsquery(value):
    """A safe OR query for PostgreSQL's simple full-text configuration."""
    return ' | '.join(re.sub(r'[^\wəƏöÖüÜğĞşŞçıİ-]', '', term) for term in terms(value) if term) or ''


def patterns(value):
    return ['%' + term.replace('%', '').replace('_', '') + '%' for term in terms(value)]
