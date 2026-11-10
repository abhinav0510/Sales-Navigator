from app.modules.ai.text_match import mentions


def test_whole_words_only():
    assert mentions("We fund EV charging startups", "ev")
    assert not mentions("Every event matters", "ev")
    assert mentions("A single-family office in Mumbai", "family office")
    assert not mentions("anything", "")
