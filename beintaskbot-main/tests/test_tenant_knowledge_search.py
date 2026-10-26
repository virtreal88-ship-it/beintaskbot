from tenant_knowledge_search import patterns, terms, tsquery


def test_warehouse_transfer_terms_expand_across_az_ru_en_without_provider_call():
    value=set(terms('Как перевести товар из одного склада в другой?'))
    assert {'склад','warehouse','anbar','перевести','transfer','yerdəyişmə','товар','məhsul'} <= value
    assert '|' in tsquery('Anbarlar arasında yerdəyişmə')
    assert all(item.startswith('%') and item.endswith('%') for item in patterns('warehouse transfer'))


def test_unrelated_query_stays_bounded():
    assert len(terms(' '.join('question' for _ in range(99)))) <= 24


def test_ascii_suffix_and_uppercase_az_expand_to_indexed_unicode():
    for query in ('sayimda', 'sayımda', 'SAYIMDA', 'İNVENTARİZASİYADA'):
        assert 'sayım' in terms(query)
    assert 'fərq' in terms('sayim ferqleri')


def test_scaffolding_and_unrelated_generic_actions_do_not_expand_inventory():
    assert terms('bu nə və necə olar')==[]
    assert 'saxla' not in terms('Как сохранить пароль?')
    assert 'müsbət' not in terms('Положительный анализ крови')
    assert patterns('analiz')==[]
    assert all(len(p)>5 for p in patterns('mal anbar'))
