"""The one block list: generous matching, because blocking too much is the safe mistake."""
from llm.privacy import PRIVATE_LABEL, group_of, is_blocked, norm, shown


def test_names_are_compared_as_words_in_both_directions():
    assert is_blocked("Google Chrome", ["Chrome"])
    assert is_blocked("chrome.exe", ["Google Chrome"])
    assert is_blocked("1Password 7", ["1Password"])
    assert is_blocked("google-chrome", ["Google Chrome"])
    assert not is_blocked("Gmail", ["Mail"]) and not is_blocked("Xcode", ["Code"])
    assert not is_blocked(None, ["Mail"]) and not is_blocked("Mail", [])


def test_invisible_marks_and_suffixes_are_ignored():
    assert norm("‎WhatsApp") == "whatsapp" == norm("WhatsApp.exe")
    assert norm("Code.app") == "code"


def test_groups_and_the_label_a_ui_shows():
    assert group_of("1Password 7") == "password" and group_of("Slack") == "messaging"
    assert group_of("Thunderbird") == "mail" and group_of("Firefox") == "browser" and group_of("Code") == "other"
    assert shown("Signal", ["Signal"]) == PRIVATE_LABEL and shown("Code", ["Signal"]) == "Code"
