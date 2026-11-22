from arche_lib.audit_jvm import build_jvm_audit
from test_graph_multilang import _graph

SOURCES = {
    'src/a/Svc.java': '''package a;
import javax.inject.Inject;
public class Svc {
    private static final long serialVersionUID = 1L;
    private int used = 1;
    private int unusedField;
    @Inject
    private Helper injected;
    private final java.util.Map<String, Integer> cache = new java.util.HashMap<>();
    private void unusedMethod(int x) {}
    private void called() { used++; cache.clear(); }
    @Deprecated private void annotated() {}
    private static class Dead {}
    public void run() { called(); String s = "byName"; }
    private void byName() {}
    private Svc() {}
}
class Helper {}
class Orphan {}
class FromXml {}
''',
    'src/a/K.kt': '''package a
private fun unusedTop() = 1
private val limit = 3
internal class Hidden
internal class Shown
class Page {
    private fun helper() = limit
    private var stale = 0
    @JvmField private var kept = 0
    fun go() = helper() + Shown().hashCode()
}
''',
    'src/test/a/T.kt': 'package a\nprivate fun nope() = 1\ninternal class TestOnly\n',
    'res/config.xml': '<bean class="a.FromXml"/>',
}


def test_only_names_mentioned_nowhere_else_are_candidates():
    inventory = {'files': [{'path': path, 'bytes': len(text)} for path, text in SOURCES.items()]}
    findings = build_jvm_audit(_graph(SOURCES), inventory, SOURCES.get)
    assert [(f['rule'], f['path'], f['name'], f['line']) for f in findings] == [
        ('unused-nonpublic-type', 'src/a/K.kt', 'Hidden', 4),
        ('unused-nonpublic-type', 'src/a/Svc.java', 'Orphan', 19),
        ('unused-private-member', 'src/a/K.kt', 'unusedTop', 2),
        ('unused-private-member', 'src/a/K.kt', 'stale', 8),
        ('unused-private-member', 'src/a/Svc.java', 'unusedField', 6),
        ('unused-private-member', 'src/a/Svc.java', 'unusedMethod', 10),
        ('unused-private-member', 'src/a/Svc.java', 'Dead', 13),
    ]


def test_no_jvm_files_means_no_findings():
    assert build_jvm_audit(_graph({'a.py': 'pass\n'}), {'files': []}, {}.get) == []


def test_kotlin_string_template_counts_as_a_use():
    sources = {'a/M.kt': 'package a\nprivate const val TOKEN = "x"\nprivate const val IDLE = "y"\nval pattern = "$TOKEN/z"\n'}
    findings = build_jvm_audit(_graph(sources), {'files': []}, sources.get)
    assert [f['name'] for f in findings] == ['IDLE']
