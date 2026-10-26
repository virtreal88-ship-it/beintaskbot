"""Local, bounded multilingual query expansion for Knowledge Sources.

This is deliberately a retrieval aid, not an embedding provider.  It keeps
customer text in PostgreSQL and gives RU/AZ/EN variants of common support
terms to the existing lexical index.  A later opted-in embedding provider can
be added as a second scorer without changing access rules or the schema.
"""
import re
import unicodedata


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
    ('sayım', 'inventarizasiya', 'inventory', 'инвентаризация', 'пересчёт', 'пересчет', 'stocktaking'),
    ('qalıq', 'stock', 'balance', 'остаток'),
    ('fərq', 'difference', 'разница', 'расхождение', 'çatışmazlıq', 'çatışmayan', 'əskik', 'недостача', 'нехватка'),
)

# Generic actions/quantities alone cannot establish the inventory topic.
_CONTEXTUAL_GROUPS = (
    ('müsbət', 'artıq', 'positive', 'излишек', 'излишки', 'положительный'),
    ('mənfi', 'negative', 'отрицательный'),
    ('saxla', 'saxlamaq', 'saxlanmış', 'save', 'saved', 'сохранить', 'сохранение'),
    ('davam', 'continue', 'resume', 'продолжить', 'продолжение'),
)


# Question scaffolding must not match every document via the ILIKE fallback.
_STOPWORDS = set("""nə nəsə necə hansı harada nə vaxt zaman mən biz siz bu o bir və ya üçün ilə ki
ola olar olsa olan bilər bilirəm bilərəm olarmı var yox burda burada proqram proqramda
is are the a an to of in on for it can how what when where i we you
как что где когда можно ли я мы вы это в во на и или из за с со для по бы
одного другой будет есть программу программе""".split())


def _fold(value):
    # Compare known domain terms across Azerbaijani keyboard spellings. Search
    # terms themselves retain Unicode so the existing simple index still works.
    return unicodedata.normalize('NFC', value.casefold()).translate(
        str.maketrans({'ə':'e','ı':'i','ö':'o','ü':'u','ğ':'g','ş':'s','ç':'c'})).replace('\u0307','')


def _matches(words, group):
    normalized={_fold(term) for term in group}
    return bool(words & normalized or any(word[:4]==term[:4] for word in words for term in normalized if len(word)>=4 and len(term)>=4))


def terms(value, limit=24):
    """Bounded lexical expansion; no provider or unrestricted fuzzy search."""
    words = re.findall(r"[\wəƏöÖüÜğĞşŞçıİ-]+", str(value or '').casefold().replace('\u0307',''))[:48]
    stopwords = {_fold(word) for word in _STOPWORDS}
    words = [word for word in words if len(word)>=3 and _fold(word) not in stopwords][:12]
    originals = {_fold(word) for word in words}
    context=_matches(originals,('sayım','inventarizasiya','inventory','инвентаризация',
        'пересчёт','пересчет','stocktaking','anbar','warehouse','склад','məhsul','product','товар','mal','qalıq','stock','остаток','sənəd','document','документ'))
    workflow=sum(_matches(originals,g) for g in _CONTEXTUAL_GROUPS[2:])>=2
    allowed_groups=list(_GROUPS)+[g for i,g in enumerate(_CONTEXTUAL_GROUPS) if context or (i>=2 and workflow)]
    if not context:
        excluded=[g for i,g in enumerate(_CONTEXTUAL_GROUPS) if i<2 or not workflow]
        words=[w for w in words if not any(_matches({_fold(w)},g) for g in excluded)]
    ordered = list(dict.fromkeys(words))
    seen = set(ordered)
    for group in allowed_groups:
        # Expanded terms never recursively trigger other groups.
        if _matches(originals,group):
            for term in group:
                term = term.casefold()
                if term not in seen:
                    seen.add(term)
                    ordered.append(term)
    return ordered[:max(1,min(int(limit),24))]


def tsquery(value):
    """A safe OR query for PostgreSQL's simple full-text configuration."""
    return ' | '.join(re.sub(r'[^\wəƏöÖüÜğĞşŞçıİ-]', '', term) for term in terms(value) if term) or ''


def patterns(value):
    # Substring fallback is for known domain variants, not arbitrary question
    # words (e.g. analiz must not match a video's analizi disclaimer).
    vocabulary={_fold(term) for group in _GROUPS+_CONTEXTUAL_GROUPS for term in group}
    return ['%' + term.replace('%', '').replace('_', '') + '%' for term in terms(value)
            if len(term)>=4 and _fold(term) in vocabulary]
