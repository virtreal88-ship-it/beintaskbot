from tenant_knowledge_search import patterns, terms, tsquery


def test_warehouse_transfer_terms_expand_across_az_ru_en_without_provider_call():
    value=set(terms('Как перевести товар из одного склада в другой?'))
    assert {'склад','warehouse','anbar','перевести','transfer','yerdəyişmə','товар','məhsul'} <= value
    assert '|' in tsquery('Anbarlar arasında yerdəyişmə')
    assert all(item.startswith('%') and item.endswith('%') for item in patterns('warehouse transfer'))


def test_unrelated_query_stays_bounded():
    assert len(terms(' '.join('question' for _ in range(99)))) <= 24
