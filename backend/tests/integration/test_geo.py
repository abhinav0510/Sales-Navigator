"""Region resolver against the seeded built-in place table. Needs PostgreSQL."""
import pytest

from app.modules.geo.resolver import company_in_scope, resolve

pytestmark = pytest.mark.integration


def test_state(db):
    r = resolve("Maharashtra")
    assert r.status == "resolved" and r.scopes[0].level == "state" and r.scopes[0].admin1_code == "IN-MH"


def test_alias_and_list(db):
    r = resolve("Bombay, Bangalore and Poona")
    assert r.status == "resolved"
    assert [s.city for s in r.scopes] == ["Mumbai", "Bengaluru", "Pune"]


def test_name_with_and_is_not_split(db):
    r = resolve("Jammu and Kashmir")
    assert r.status == "resolved" and len(r.scopes) == 1 and r.scopes[0].admin1_code == "IN-JK"


def test_radius_both_phrasings(db):
    for text in ("within 25 km of Pune", "Pune 25km"):
        r = resolve(text)
        assert r.scopes[0].level == "radius" and r.scopes[0].radius_km == 25 and r.scopes[0].city is not None


def test_ambiguous_city(db):
    r = resolve("Aurangabad")
    assert r.status == "ambiguous"
    assert {o["admin1_code"] for o in r.ambiguities[0]["options"]} == {"IN-MH", "IN-BR"}


def test_global_market_list(db):
    r = resolve("global")
    assert [s.country_iso2 for s in r.scopes][:3] == ["IN", "US", "GB"]


def test_unresolved_never_loops(db):
    first = resolve("Baner near Balewadi", attempt=1)
    assert first.status == "unresolved" and first.forced_choice is False and first.allow_map_pin
    second = resolve("Baner near Balewadi", attempt=2)
    assert second.forced_choice is True          # UI removes the free-text box: no dead-end loop


def test_did_you_mean(db):
    r = resolve("Maharastra")                    # typo
    assert r.status == "unresolved"
    assert any(s["admin1_code"] == "IN-MH" for s in r.unresolved[0]["did_you_mean"])


def test_scope_membership(db):
    pune_radius = resolve("within 25 km of Pune").scopes[0].__dict__
    assert company_in_scope({"city": "Pune", "admin1_code": "IN-MH", "country_iso2": "IN"}, [pune_radius])
    assert not company_in_scope({"city": "Mumbai", "admin1_code": "IN-MH", "country_iso2": "IN"}, [pune_radius])
    mh = resolve("Maharashtra").scopes[0].__dict__
    assert not company_in_scope({"city": "Ahmedabad", "admin1_code": "IN-GJ", "country_iso2": "IN"}, [mh])
