"""Column aggregates: types, nulls, distinct counts, ranges, shapes and sensitivity."""
import datetime as dt
import re
import sys
from decimal import Decimal
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "skills/arche/scripts"))
from arche_lib.data import stats as module
from arche_lib.data.stats import HARMLESS_WORDS, SENSITIVE_NAMES, ColumnStats, classify, sensitive_name, shape


def _summary(name, values, rows=None, show_values=False):
    column = ColumnStats(name, show_values)
    for value in values:
        column.add(value)
    return column.summary(len(values) if rows is None else rows)


@pytest.mark.parametrize("value, kind", [
    ("", "null"), ("NULL", "null"), ("\\N", "null"), ("nan", "null"), (None, "null"), (float("nan"), "null"),
    ("TRUE", "boolean"), (True, "boolean"), ("42", "integer"), ("-7", "integer"), (3, "integer"),
    ("007", "string"), ("0", "integer"), ("1" * 19, "decimal"), ("1.5", "decimal"), ("1e5", "decimal"),
    (2.5, "decimal"), (Decimal("1.1"), "decimal"), ("2026-11-25", "date"), ("2026-02-30", "string"),
    (dt.date(2026, 1, 1), "date"), ("2026-11-25T10:00:00Z", "timestamp"), ("2026-11-25 25:00", "string"),
    (dt.datetime(2026, 1, 1, 1), "timestamp"), ({"a": 1}, "object"), ([1], "array"), (b"x", "binary"),
    ("Kim", "string"),
])
def test_value_types(value, kind):
    assert classify(value)[0] == kind


def test_a_type_needs_95_percent_of_values_and_integers_merge_into_decimals():
    assert _summary("n", ["1"] * 19 + ["x"])["type"] == "integer"
    mixed = _summary("n", ["1"] * 9 + ["x"])
    assert mixed["type"] == "mixed" and mixed["types"] == [["integer", 0.9], ["string", 0.1]]
    assert _summary("n", ["1", "2.5"])["type"] == "decimal"
    assert _summary("n", [None, ""])["type"] == "unknown"


def test_nulls_include_rows_where_the_column_is_missing():
    result = _summary("n", ["1", ""], rows=4)

    assert result["null_ratio"] == 0.75 and result["candidate_key"] is False


def test_ranges_compare_by_type_and_keep_the_original_text():
    result = _summary("n", ["10", "9", "1e1", "-2"])
    assert (result["min"], result["max"]) == ("-2", "10")
    dates = _summary("d", ["2026-11-02", "2026-01-15"])
    assert (dates["min"], dates["max"]) == ("2026-01-15", "2026-11-02")
    assert "min" not in _summary("s", ["b", "a"])


def test_candidate_keys_need_distinct_non_null_values_and_two_rows():
    assert _summary("id", ["1", "2", "3"])["candidate_key"] is True
    assert _summary("id", ["1", "1"])["candidate_key"] is False
    assert _summary("id", ["1"])["candidate_key"] is False


def test_distinct_counts_stop_at_the_limit(monkeypatch):
    monkeypatch.setattr(module, "DISTINCT_LIMIT", 3)
    result = _summary("id", [str(i) for i in range(5)])

    assert (result["distinct"], result["distinct_capped"], result["candidate_key"]) == (3, True, False)


def test_shapes_hide_letters_and_digits():
    assert shape("kim.hong@example.com") == "aaa.aaaa@a{7}.aaa"
    assert shape("AB-1234 김") == "AA-9999 ?"
    assert len(shape("x" * 100 + "-" * 100)) <= 40


def test_string_values_appear_only_as_shapes_unless_asked():
    hidden = _summary("code", ["AB-1", "AB-2", "CD-3"])
    assert hidden["patterns"] == [["AA-9", 3]] and "top_values" not in hidden

    shown = _summary("code", ["AB-1", "AB-1", "CD-3"], show_values=True)
    assert shown["top_values"] == [["AB-1", 2], ["CD-3", 1]]


@pytest.mark.parametrize("name", ["email", "User_Email", "phone_no", "api-key", "birth_date", "고객주소", "card"])
def test_sensitive_names(name):
    assert sensitive_name(name) is True


@pytest.mark.parametrize("name", ["pan", "card_pan", "tel", "dob", "card", "cardNo", "credit_card", "ssn", "user_email",
                                  "emailAddress", "phone_number", "phoneNumber", "api-key", "birth_date", "고객주소",
                                  "telno", "TEL_NO", "access_token", "accessToken", "userPAN"])
def test_short_names_match_whole_tokens(name):
    assert sensitive_name(name) is True


@pytest.mark.parametrize("name", ["company", "span", "japan", "hotel", "telemetry", "adobe", "discard", "intel",
                                  "tokenizer", "cardinality", "panel", "phonetic", "vibrant"])
def test_short_names_inside_longer_words_are_not_sensitive(name):
    assert sensitive_name(name) is False


GLUED = ["customerphone", "userphone", "homephone", "workphone", "contactphone", "cellphone", "CUSTOMERPHONE", "hometel",
         "officetel", "faxtel", "mytel", "contacttel", "custtel", "creditcard", "debitcard", "cardholder", "cardholder_name",
         "cardid", "cardtype", "fullpan", "panhash", "devicetoken", "pushtoken", "fcmtoken", "jwttoken", "sessiontoken",
         "bankiban", "userdob", "dobyear", "ssnumber", "phones", "user_phones", "phonenumbers", "cards", "tokens", "TELS",
         "telefon", "discard_card", "TelNo", "pan_last4", "ssns", "dobDate", "spanssn", "hotelssecret", "panelssn"]


@pytest.mark.parametrize("name", GLUED)
def test_glued_and_plural_names_stay_sensitive(name):
    assert sensitive_name(name) is True


@pytest.mark.parametrize("name", ["intel", "phonetic", "phonetic_spelling", "tokenizer", "cardinality", "discarded",
                                  "tokenization_ms", "phonetics", "spans", "hotels", "panels", "pandas"])
def test_known_harmless_words_are_not_sensitive(name):
    assert sensitive_name(name) is False


def _old_rule(name):
    old = ("email", "phone", "mobile", "tel", "ssn", "passport", "password", "passwd", "secret", "token", "apikey", "card",
           "pan", "ccnum", "cardno", "iban", "accountno", "birth", "dob", "address", "주민", "전화", "휴대", "생년", "주소",
           "계좌", "여권")
    normalized = re.sub(r"[\s_.\-]", "", name.lower())
    return any(part in normalized for part in old)


EXPECTED_FALSE_POSITIVES = {"company", "span", "japan", "hotel", "intel", "telemetry", "adobe", "discard", "phonetic",
                            "tokenizer", "cardinality", "panel", "spanish", "company_name", "hotel_id", "adobe_id",
                            "telemetry_ms", "japan_region", "phonetic_spelling", "discarded"}
SENSITIVE_LOOKING = GLUED + ["email", "User_Email", "emailAddress", "phone_no", "phone_number", "phoneNumber", "api-key",
                             "api_key", "birth_date", "고객주소", "card", "cardNo", "credit_card", "ssn", "pan", "card_pan",
                             "tel", "dob", "password", "passwd", "secret_key", "passport_no", "address_line", "iban",
                             "account_no", "ccnum", "mobile", "mobile_no", "주민번호", "전화번호", "휴대폰", "생년월일",
                             "계좌번호", "여권번호", "access_token", "accessToken", "auth_token", "tel_no", "telephone"]


GLUED_MORE = ["patientdob", "empdob", "childdob", "parentdob", "rawdob", "dobmonth", "dobday", "dobtext", "dobyyyy",
              "empssn", "payerssn", "patientssn", "encryptedssn", "ssnenc", "primarypan", "bankpan", "taxpan",
              "guardiantel", "parenttel", "emergencytel", "shoptel", "companytel", "telnos", "company_tel", "companyTel"]


@pytest.mark.parametrize("name", GLUED_MORE)
def test_any_glue_around_a_short_name_stays_sensitive(name):
    assert sensitive_name(name) is True


@pytest.mark.parametrize("word", HARMLESS_WORDS)
def test_each_harmless_word_alone_is_not_sensitive(word):
    assert sensitive_name(word) is False and sensitive_name(word.upper()) is False


@pytest.mark.parametrize("word", HARMLESS_WORDS)
def test_each_harmless_word_contains_a_sensitive_entry(word):
    assert any(entry in word for entry in SENSITIVE_NAMES)


def test_the_new_rule_equals_the_old_substring_rule_minus_names_made_only_of_harmless_words():
    names = SENSITIVE_LOOKING + GLUED_MORE + sorted(EXPECTED_FALSE_POSITIVES) + list(HARMLESS_WORDS)
    assert len(SENSITIVE_LOOKING) >= 60
    harmless_only = EXPECTED_FALSE_POSITIVES | set(HARMLESS_WORDS)
    mismatched = [n for n in names if sensitive_name(n) != (_old_rule(n) and n not in harmless_only)]
    assert mismatched == []


def test_sensitive_columns_never_carry_values_or_ranges():
    by_name = _summary("birth_date", ["2000-01-01", "1990-05-05"], show_values=True)
    assert "min" not in by_name and "top_values" not in by_name and by_name["sensitive"] is True

    by_value = _summary("contact", ["a@x.com", "b@y.org", "010-1234-5678", "n/a"], show_values=True)
    assert by_value["sensitive"] is True and "top_values" not in by_value
    assert all("@x.com" not in pattern for pattern, _ in by_value["patterns"])


CARDS = ["4111111111111111", "5500000000000004"]


def test_a_card_named_column_has_no_range():
    summary = _summary("pan", CARDS)

    assert summary["sensitive"] is True and "min" not in summary and "max" not in summary


def test_card_numbers_are_sensitive_by_value_whatever_the_name():
    summary = _summary("ref", CARDS * 5)

    assert summary["type"] == "integer"
    assert summary["sensitive"] is True and "min" not in summary


def test_ordinary_integers_are_not_sensitive():
    assert _summary("id", [str(n) for n in range(1, 101)])["sensitive"] is False
    orders = [str(1234567000 + n) for n in range(1, 41)]   # 10 digits, no contact name, not Luhn
    assert not any(module.luhn(order) for order in orders)
    assert _summary("order_no", orders)["sensitive"] is False


def test_digit_only_phone_numbers_count_only_by_name():
    assert _summary("contact", ["0212345678"] * 3)["sensitive"] is True    # leading zero: a string shaped like a phone
    assert _summary("order_no", ["1234567891"] * 3)["sensitive"] is False   # an integer; the rule leaves it alone
    assert _summary("tel_no", ["1234567891"] * 3)["sensitive"] is True
