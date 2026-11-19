import pytest

from arche_lib.langs import dart_resolve

PUBSPEC = "name: shop_app\ndescription: demo\nenvironment:\n  sdk: ^3.0.0\n"


def _ctx(files, parsed=None):
    parsed = [p for p in files if p.endswith(".dart")] if parsed is None else parsed
    return dart_resolve.build_context(parsed, list(files), files.get)


def _imp(uri, names=(), library=None):
    return {"kind": "import", "uri": uri, "library": library, "names": list(names), "attrs": {}}


FILES = {
    "pubspec.yaml": PUBSPEC,
    "lib/main.dart": "",
    "lib/src/app.dart": "",
    "lib/src/models/order.dart": "",
    "lib/src/models/order.g.dart": "",
    "test/order_test.dart": "",
}
PARSED = [p for p in FILES if p.endswith(".dart") and not p.endswith(".g.dart")]


def _resolve(uri, importer, names=()):
    return dart_resolve.resolve(_imp(uri, names), importer, _ctx(FILES, PARSED))


def test_pubspec_name_is_read_for_package_lookup():
    assert _ctx(FILES).packages == {"shop_app": ""}


def test_nested_pubspec_maps_its_own_directory():
    ctx = _ctx({"apps/mobile/pubspec.yaml": "name: demo_mobile\n", "apps/mobile/lib/a.dart": ""})

    assert ctx.packages == {"demo_mobile": "apps/mobile"}


def test_relative_uri_resolves_against_the_importing_file():
    assert _resolve("src/app.dart", "lib/main.dart", ["App"]) == [
        {"status": "verified", "to": "lib/src/app.dart", "names": ["App"]}
    ]
    assert _resolve("../lib/src/models/order.dart", "test/order_test.dart") == [
        {"status": "verified", "to": "lib/src/models/order.dart", "names": []}
    ]


def test_own_package_uri_resolves_into_lib():
    assert _resolve("package:shop_app/src/models/order.dart", "test/order_test.dart") == [
        {"status": "verified", "to": "lib/src/models/order.dart", "names": []}
    ]


@pytest.mark.parametrize(
    "uri, package",
    [
        ("dart:async", "dart:async"),
        ("package:flutter/material.dart", "package:flutter"),
        ("package:flutter_riverpod/flutter_riverpod.dart", "package:flutter_riverpod"),
    ],
)
def test_sdk_and_foreign_packages_are_external(uri, package):
    assert _resolve(uri, "lib/main.dart") == [{"status": "external", "package": package}]


def test_generated_part_file_is_target_skipped():
    assert _resolve("order.g.dart", "lib/src/models/order.dart") == [
        {"status": "unresolved", "spec": "order.g.dart", "reason": "target_skipped"}
    ]


def test_missing_file_and_escaping_uri_are_unresolved():
    assert _resolve("nope.dart", "lib/main.dart") == [
        {"status": "unresolved", "spec": "nope.dart", "reason": "module_not_found"}
    ]
    assert _resolve("../../outside.dart", "lib/main.dart") == [
        {"status": "unresolved", "spec": "../../outside.dart", "reason": "outside_root"}
    ]


def test_part_of_a_library_name_is_unresolved():
    found = dart_resolve.resolve(_imp(None, library="shop_app.legacy"), "lib/old.dart", _ctx(FILES, PARSED))

    assert found == [{"status": "unresolved", "spec": "shop_app.legacy", "reason": "library_name"}]
