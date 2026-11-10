"""Shared knowledge store: entity resolution + merge path (regression for the array-cast bug)."""
import pytest

from app.core.db import system_tx
from app.core.security import decrypt
from app.modules.knowledge import store

pytestmark = pytest.mark.integration


def test_company_upsert_merges_by_domain_and_by_name_city(db):
    with system_tx() as conn:
        a = store.upsert_company(conn, domain="mergeco.example", name="MergeCo Pvt Ltd", city="Pune",
                                 admin1_code="IN-MH", tech=["Shopify"], source="b2b_company")
        # Same domain again with new facts -> same row, facts merged (this UPDATE path once failed).
        b = store.upsert_company(conn, domain="mergeco.example", name="MergeCo", city="Pune", admin1_code="IN-MH",
                                 tech=["Shopify", "Amazon"], employees=40, source="lookalike")
        # No domain, same normalised name + city -> same company.
        c = store.upsert_company(conn, domain=None, name="Mergeco Private Limited", city="pune", admin1_code="IN-MH",
                                 tech=[], source="local_business")
        row = store.get_company(conn, a)
    assert a == b == c
    assert row["employees"] == 40 and set(row["tech"]) == {"Shopify", "Amazon"}
    assert set(row["sources"]) == {"b2b_company", "lookalike", "local_business"}


def test_contacts_are_encrypted_and_deduplicated(db):
    with system_tx() as conn:
        cid = store.upsert_company(conn, domain="enc.example", name="Enc", city="Pune", admin1_code="IN-MH")
        p1 = store.upsert_contact(conn, kind="work_email", value="a@enc.example", person_id=None, company_id=cid,
                                  verify_status="valid", confidence=0.9, source="x")
        p2 = store.upsert_contact(conn, kind="work_email", value="a@enc.example", person_id=None, company_id=cid,
                                  verify_status="valid", confidence=0.95, source="y")
        raw = conn.exec_driver_sql("SELECT value_enc FROM contact_point WHERE id = %s", (p1,)).scalar_one()
    assert p1 == p2                                   # one row per value
    assert b"a@enc.example" not in bytes(raw)          # never stored readable
    assert decrypt(raw) == "a@enc.example"
