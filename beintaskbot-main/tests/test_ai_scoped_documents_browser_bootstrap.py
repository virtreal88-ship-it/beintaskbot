"""Full-controller bootstrap/HTTP contract; JSDOM is not browser layout proof."""
from test_ai_scoped_documents_browser import run_browser,SyntheticBoundary


def test_full_controller_bootstrap_and_acceptance_over_http():
    assert 'PASS synthetic HTTP boundary' in run_browser(SyntheticBoundary(),1100,runner='ai_scoped_documents_http_dom.cjs')
