"""Normalisers, LinkedIn snippet parsing, title/persona ranking, email pattern engine."""
from app.modules.contacts.email_patterns import candidates, find_emails, learn_format, render
from app.modules.contacts.people import classify_title, parse_linkedin_result, persona_rank
from app.modules.knowledge.normalize import (company_name_key, person_name_key, phone_e164, phone_type,
                                             registrable_domain, split_name)


def test_registrable_domain():
    assert registrable_domain("https://www.shop.kosh.co.in/about") == "kosh.co.in"
    assert registrable_domain("kosh.example") == "kosh.example"
    assert registrable_domain("not a url") is None


def test_company_and_person_keys():
    assert company_name_key("Kosh Skincare Pvt. Ltd.") == "kosh skincare"
    assert company_name_key("Pedal & Co") == company_name_key("Pedal and Co")
    assert person_name_key("Dr. Ananya  Kulkarni") == "ananya kulkarni"
    assert split_name("Madonna") == ("madonna", "")


def test_phone():
    assert phone_e164("020 4100 0000") == "+912041000000"
    assert phone_type("+912041000000") == "landline"
    assert phone_e164("12") is None


def test_linkedin_snippet_parsing():
    p = parse_linkedin_result("Ananya Kulkarni - Founder & CEO - Kosh Skincare | LinkedIn",
                              "https://www.linkedin.com/in/ananya-kulkarni-kosh")
    assert (p.full_name, p.title, p.company, p.linkedin_slug) == (
        "Ananya Kulkarni", "Founder & CEO", "Kosh Skincare", "ananya-kulkarni-kosh")
    assert parse_linkedin_result("Some random page title", "https://x.example") is None


def test_title_and_persona_ranking():
    assert classify_title("Head of E-commerce") == ("head", "ecommerce")
    personas = ["Head of E-commerce", "Head of Operations", "Founder"]
    # Small company: founder decides.
    assert persona_rank("Founder & CEO", personas, employees=30) == 0
    # Larger company: ICP persona order wins over founder.
    assert persona_rank("Head of E-commerce", personas, employees=150) == 1
    assert persona_rank("Founder & CEO", personas, employees=150) == 3
    assert persona_rank("Graphic Designer", personas, employees=150) is None


def test_email_patterns():
    assert render("{first}.{last}", "Ananya", "Kulkarni") == "ananya.kulkarni"
    assert render("{first}.{last}", "Madonna", "") is None          # single-name person
    published = find_emails("Press: write to sneha.joshi@brand.example or info@brand.example", "brand.example")
    assert published == ["sneha.joshi@brand.example", "info@brand.example"]
    assert learn_format(published, [("sneha", "joshi")]) == "{first}.{last}"
    c = candidates("Rohan", "Mehta", "brand.example", learned="{f}{last}")
    assert c[0] == "rmehta@brand.example" and len(c) <= 4 and len(set(c)) == len(c)
