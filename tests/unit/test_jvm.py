from arche_lib.langs.jvm import extract
from test_graph_multilang import _edge, _graph


def test_java_imports_symbols_and_masked_text():
    text = '''package com.a.b;
// import com.a.Fake;
import static com.a.util.Money.cents;
import com.a.model.*;
import com.a.model.Order.Line;
@Deprecated
public final class Service {
    String s = "import com.a.No;";
    class Inner {}
}
interface Helper {}
'''
    result = extract('src/com/a/b/Service.java', text)
    explicit = [(i['spec'], i['line']) for i in result['imports'] if not i.get('implicit')]
    assert explicit == [('com.a.util.Money.cents', 3), ('com.a.model.*', 4), ('com.a.model.Order.Line', 5)]
    assert result['package'] == 'com.a.b'
    assert [(s['name'], s['kind'], s['exported']) for s in result['symbols']] == [
        ('Service', 'class', True), ('Helper', 'interface', False)]


def test_kotlin_declarations_and_aliases():
    text = '''package a.b
import a.c.Thing as Other
/* /* nested */ import a.c.Hidden */
data class Point(val x: Int)
enum class Mode { ON }
fun interface Handler { fun run() }
internal object Registry
fun <T> List<T>.second(): T = this[1]
private val limit = 3
val (left, right) = pair
typealias Names = List<String>
class Outer { fun inner() {} }
'''
    result = extract('a/b/File.kt', text)
    imports = [i for i in result['imports'] if not i.get('implicit')]
    assert [(i['spec'], i['attrs']) for i in imports] == [('a.c.Thing', {'alias': 'Other'})]
    assert [(s['name'], s['kind'], s['exported']) for s in result['symbols']] == [
        ('Point', 'class', True), ('Mode', 'enum', True), ('Handler', 'interface', True),
        ('Registry', 'class', False), ('second', 'function', True), ('limit', 'variable', False),
        ('Names', 'typedef', True), ('Outer', 'class', True)]


def test_java_and_kotlin_resolve_into_each_other():
    graph = _graph({
        'src/main/java/a/Api.java': 'package a;\nimport a.impl.Engine;\nimport a.impl.Engine.Part;\nimport a.impl.Gone;\nimport java.util.List;\npublic class Api { Local local; }\n',
        'src/main/java/a/Local.java': 'package a;\nclass Local {}\n',
        'src/main/kotlin/a/impl/Engine.kt': 'package a.impl\nimport a.*\nclass Engine { val api = Api() }\nfun start() {}\n',
        'src/main/kotlin/a/Use.kt': 'package a\nimport a.impl.start\nfun use() = start()\n',
    })
    api, engine = 'src/main/java/a/Api.java', 'src/main/kotlin/a/impl/Engine.kt'
    forward = _edge(graph, api, engine)
    assert forward['status'] == 'verified' and forward['rule'] == 'jvm_qualified_name'
    assert _edge(graph, 'src/main/kotlin/a/Use.kt', engine)['status'] == 'verified'
    same_package = _edge(graph, api, 'src/main/java/a/Local.java')
    assert same_package['status'] == 'inferred' and same_package['rule'] == 'jvm_same_package'
    assert same_package['evidence']['line'] == 6
    # The wildcard keeps only the file whose type the importer names.
    wildcard = [e for e in graph['edges'] if e['from'] == 'file:' + engine]
    assert [(e['to'], e['status'], e['rule']) for e in wildcard] == [('file:' + api, 'inferred', 'jvm_package_wildcard')]
    assert [(e['spec'], e['reason']) for e in graph['edges'] if e['status'] == 'unresolved'] == [('a.impl.Gone', 'symbol_not_found')]
    assert [(x['from'], x['package']) for x in graph['externals']] == [('file:' + api, 'java.util')]


def test_same_name_in_two_source_sets_stays_a_candidate():
    graph = _graph({
        'main/a/Config.kt': 'package a\nclass Config\n',
        'debug/a/Config.kt': 'package a\nclass Config\n',
        'main/b/App.kt': 'package b\nimport a.Config\nval c = Config()\n',
    })
    edges = [e for e in graph['edges'] if e['from'] == 'file:main/b/App.kt']
    assert sorted(e['to'] for e in edges) == ['file:debug/a/Config.kt', 'file:main/a/Config.kt']
    assert {e['status'] for e in edges} == {'inferred'}
