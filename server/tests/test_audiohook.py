from app.providers.genesys.audiohook import select_media, validate_api_key

EXT = {"format": "PCMU", "channels": ["external"]}
EI = {"format": "PCMU", "channels": ["external", "internal"]}
IE = {"format": "PCMU", "channels": ["internal", "external"]}


def test_select_media_honors_preference_order():
    prefs = [["external", "internal"], ["internal", "external"], ["external"]]
    assert select_media([EXT, IE, EI], prefs) is EI
    assert select_media([EXT, IE], prefs) is IE
    assert select_media([EXT], prefs) is EXT


def test_select_media_falls_back_to_first_pcmu_or_none():
    assert select_media([{"format": "L16"}, IE], [["external"]]) is IE
    assert select_media([{"format": "L16"}], [["external"]]) is None


def test_validate_api_key():
    assert validate_api_key("a", "a")
    assert not validate_api_key("a", "b")
    assert not validate_api_key("", "")
