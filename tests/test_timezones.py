from core.timezones import DEFAULT_TIMEZONE, get_zone, normalize_timezone_name


def test_legacy_kyiv_timezone_name_uses_canonical_iana_zone() -> None:
    assert get_zone("Europe/Kiev").key == "Europe/Kyiv"


def test_canonical_kyiv_timezone_name_remains_supported() -> None:
    assert get_zone("Europe/Kyiv").key == "Europe/Kyiv"


def test_legacy_kyiv_timezone_name_is_normalized_before_storage() -> None:
    assert normalize_timezone_name(" Europe/Kiev ") == "Europe/Kyiv"
    assert normalize_timezone_name("Europe/Warsaw") == "Europe/Warsaw"


def test_default_timezone_uses_canonical_iana_name() -> None:
    assert DEFAULT_TIMEZONE == "Europe/Kyiv"
    assert normalize_timezone_name(None) == "Europe/Kyiv"
