import hashlib

from arche_lib.changes import _dependents, _reverse_imports
from arche_lib.graph import build_graph
from arche_lib.inventory import SECRET_PATTERNS, VENDORED_DIRS, _matches, count_lines
from arche_lib.langs import language_for, terraform
from arche_lib.langs.hcl_lexer import tokens

ROOT = '''# root module
terraform { required_version = ">= 1.5" }
module "vpc" {
  source = "./modules/vpc"
  tags   = { source = "not-this" }
}
module "eks" {
  source  = "terraform-aws-modules/eks/aws"
  version = "~> 20.0"
}
resource "aws_s3_bucket" "logs" {
  policy = <<-EOT
    module "fake" { source = "./heredoc" }
  EOT
  bucket = "${var.env}-logs"
}
/* module "old" { source = "./commented" } */
variable "env" {}
output "bucket" { value = aws_s3_bucket.logs.id }
data "aws_iam_policy" "p" { name = "x" }
'''


def _sources(text, path="infra/main.tf"):
    return [(imp["spec"], imp["line"]) for imp in terraform.extract(path, text)["imports"]]


def test_language_for_tf():
    assert language_for("infra/main.tf") == "terraform"
    assert language_for("infra/prod.tfvars") is None


def test_only_the_module_source_attribute_is_an_import():
    assert _sources(ROOT) == [("./modules/vpc", 4), ("terraform-aws-modules/eks/aws", 8)]


def test_top_level_blocks_are_named_symbols():
    symbols = terraform.extract("infra/main.tf", ROOT)["symbols"]

    assert [(s["name"], s["kind"], s["line"], s["exported"]) for s in symbols] == [
        ("module.vpc", "module", 3, False),
        ("module.eks", "module", 7, False),
        ("aws_s3_bucket.logs", "resource", 11, False),
        ("var.env", "variable", 18, True),
        ("output.bucket", "output", 19, True),
        ("data.aws_iam_policy.p", "data", 20, False),
    ]


def test_local_source_records_the_module_directory():
    imp = terraform.extract("infra/main.tf", ROOT)["imports"][0]

    assert imp["attrs"] == {"module_dir": "infra/modules/vpc"}
    assert imp["dynamic"] is False


def test_templated_source_is_dynamic():
    imp = terraform.extract("main.tf", 'module "m" { source = "./modules/${var.name}" }\n')["imports"][0]

    assert imp["dynamic"] is True
    assert "attrs" not in imp


def test_lexer_keeps_escaped_template_markers_literal():
    toks, broken = tokens('x = "$${not} %%{no} \\"q\\" ${a["b"]}"\n')

    assert not broken
    assert toks[2].value == '${not} %{no} "q" ${}'
    assert toks[2].templated is True


def test_unterminated_heredoc_degrades_and_keeps_earlier_blocks():
    result = terraform.extract("main.tf", 'module "a" { source = "./a" }\nlocals {\n  x = <<EOT\nnever ends\n')

    assert result["degraded"] is True
    assert [imp["spec"] for imp in result["imports"]] == ["./a"]


def _ctx(paths):
    return terraform.build_context([p for p in paths if p.endswith(".tf")], paths, lambda rel: None)


def _resolve(spec, importer, ctx):
    imp = terraform.extract(importer, f'module "m" {{ source = "{spec}" }}\n')["imports"][0]
    return terraform.resolve(imp, importer, ctx)


def test_local_module_resolves_to_main_tf():
    ctx = _ctx(["infra/main.tf", "infra/modules/vpc/variables.tf", "infra/modules/vpc/main.tf"])

    assert _resolve("./modules/vpc", "infra/main.tf", ctx) == [
        {"status": "verified", "to": "infra/modules/vpc/main.tf", "names": [], "rule": "module_dir"}]


def test_module_without_main_tf_resolves_to_the_first_file_by_name():
    ctx = _ctx(["envs/prod/main.tf", "modules/net/outputs.tf", "modules/net/network.tf"])

    assert _resolve("../../modules/net", "envs/prod/main.tf", ctx)[0]["to"] == "modules/net/network.tf"


def test_directory_without_tf_files_is_not_found():
    ctx = _ctx(["main.tf", "modules/vpc/README.md"])

    assert _resolve("./modules/vpc", "main.tf", ctx) == [
        {"status": "unresolved", "spec": "./modules/vpc", "reason": "module_not_found"}]


def test_source_above_the_repository_root_is_not_found():
    ctx = _ctx(["main.tf"])

    assert _resolve("../outside", "main.tf", ctx)[0]["reason"] == "module_not_found"


def test_remote_sources_are_external_packages():
    cases = {
        "hashicorp/consul/aws": "hashicorp/consul/aws",
        "app.terraform.io/org/vpc/aws": "app.terraform.io/org/vpc/aws",
        "git::https://example.com/net.git//modules/sub?ref=v1.2": "git::https://example.com/net.git",
        "github.com/org/repo//mod?ref=v1": "github.com/org/repo",
        "git@github.com:org/repo.git//mod?ref=x": "git@github.com:org/repo.git",
        "s3::https://s3.amazonaws.com/bucket/vpc.zip": "s3::https://s3.amazonaws.com/bucket/vpc.zip",
    }
    ctx = _ctx(["main.tf"])
    for spec, package in cases.items():
        assert _resolve(spec, "main.tf", ctx) == [{"status": "external", "package": package}], spec


def test_state_and_variable_files_are_secret_and_dot_terraform_is_vendored():
    for name in ("prod.tfvars", "prod.tfvars.json", "terraform.tfstate", "terraform.tfstate.backup"):
        assert _matches(name, SECRET_PATTERNS), name
    assert ".terraform" in VENDORED_DIRS


SOURCES = {
    "infra/main.tf": 'module "vpc" {\n  source = "./modules/vpc"\n}\n',
    "infra/modules/vpc/main.tf": 'resource "aws_vpc" "this" {}\n',
    "infra/modules/vpc/variables.tf": 'variable "cidr" {}\n',
}


def _graph(sources):
    files = [{"path": path, "status": "analyzed", "lang": language_for(path), "bytes": len(text),
              "lines": count_lines(text), "sha256": hashlib.sha256(text.encode()).hexdigest()}
             for path, text in sources.items()]
    inventory = {"schema_version": "0.1", "repo": {"name": "demo", "root": "/abs/demo", "commit": None, "dirty": None},
                 "files": files}
    return build_graph(inventory, sources.get)


def test_graph_edge_carries_the_module_directory():
    edge = next(e for e in _graph(SOURCES)["edges"] if e["from"] == "file:infra/main.tf")

    assert (edge["kind"], edge["status"], edge["rule"], edge["to"], edge["module_dir"]) == (
        "IMPORTS", "verified", "module_dir", "file:infra/modules/vpc/main.tf", "infra/modules/vpc")


def test_a_change_to_any_file_of_the_module_reaches_its_caller():
    reverse = _reverse_imports(_graph(SOURCES))

    assert _dependents(reverse, "infra/modules/vpc/variables.tf") == ["infra/main.tf"]
    assert _dependents(reverse, "infra/modules/vpc/main.tf") == ["infra/main.tf"]


FILE_READS = '''locals {
  workspaces = { for f in fileset(path.module, "workspaces/**/*.yaml") : f => yamldecode(file("${path.module}/${f}")) }
  policy     = file("${path.module}/policies/base.json")
  user_data  = templatefile("templates/user_data.sh.tpl", { name = var.name })
  teams      = fileset("${path.root}/teams", "*.{yml,yaml}")
  dynamic    = file("${var.dir}/x.json")
  formatted  = file(format("%s/a.json", path.module))
}
'''


def test_file_functions_become_path_references():
    refs = terraform.extract("onboarding/main.tf", FILE_READS)["path_refs"]

    assert [(r["via"], r["spec"], r["base"], r["target"], r.get("inferred", False)) for r in refs] == [
        ("terraform:fileset", "workspaces/**/*.yaml", "onboarding", "glob", False),
        ("terraform:file", "${}", "onboarding", "file", False),
        ("terraform:file", "policies/base.json", "onboarding", "file", False),
        ("terraform:templatefile", "templates/user_data.sh.tpl", "onboarding", "file", True),
        ("terraform:fileset", "teams/*.{yml,yaml}", "onboarding", "glob", True),
        ("terraform:file", "${}/x.json", "onboarding", "file", True),
    ]


def test_path_module_template_is_not_a_dynamic_module_source():
    toks, _ = tokens('x = "${path.module}/a" y = "${var.a}/b"\n')

    assert (toks[2].value, toks[2].templated) == ("${path.module}/a", False)
    assert (toks[5].value, toks[5].templated) == ("${}/b", True)


ONBOARDING = {
    "onboarding/main.tf": FILE_READS,
    "onboarding/workspaces/team-a/app.yaml": "name: app\n",
    "onboarding/workspaces/team-b/db.yaml": "name: db\n",
    "onboarding/workspaces/README.md": "docs\n",
    "onboarding/policies/base.json": "{}\n",
    "onboarding/templates/user_data.sh.tpl": "#!/bin/sh\n",
    "onboarding/teams/a.yml": "x: 1\n",
}


def _onboarding_graph():
    files = []
    for path, text in ONBOARDING.items():
        measured = {"bytes": len(text), "lines": count_lines(text), "sha256": hashlib.sha256(text.encode()).hexdigest()}
        lang = language_for(path)
        files.append({"path": path, "status": "analyzed", "lang": lang, **measured} if lang
                     else {"path": path, "status": "skipped", "reason": "unsupported_lang", **measured})
    inventory = {"schema_version": "0.1", "repo": {"name": "demo", "root": "/abs/demo", "commit": None, "dirty": None},
                 "files": files}
    return build_graph(inventory, ONBOARDING.get)


def test_fileset_links_to_its_first_match_and_records_the_pattern():
    edges = [e for e in _onboarding_graph()["edges"] if e["kind"] == "REFERENCES"]
    by_via = {(e["via"], e["status"]): e for e in edges}

    workspaces = by_via[("terraform:fileset", "verified")]
    assert (workspaces["to"], workspaces["rule"], workspaces["glob"], workspaces["matched"]) == (
        "file:onboarding/workspaces/team-a/app.yaml", "glob", "onboarding/workspaces/**/*.yaml", 2)
    teams = by_via[("terraform:fileset", "inferred")]
    assert (teams["to"], teams["matched"]) == ("file:onboarding/teams/a.yml", 1)
    assert {(e["via"], e["status"], e.get("reason")) for e in edges if e["via"] == "terraform:file"} == {
        ("terraform:file", "unresolved", "dynamic"), ("terraform:file", "unresolved", "target_skipped")}
    assert ("terraform:templatefile", "unresolved") in by_via  # a .tpl file is not parsed


def test_a_change_to_any_file_a_fileset_matches_reaches_the_caller():
    reverse = _reverse_imports(_onboarding_graph())

    assert _dependents(reverse, "onboarding/workspaces/team-b/db.yaml") == ["onboarding/main.tf"]


MODULE_ARG = {
    "onboarding/main.tf": ('module "workspaces" {\n'
                           '  source     = "./modules/workspace"\n'
                           '  config_dir = "${path.module}/workspaces"\n'
                           '  dynamic    = "${path.module}/${var.x}"\n'
                           '  self       = "${path.module}"\n'
                           '}\n'),
    "onboarding/modules/workspace/main.tf": 'locals { all = fileset(var.config_dir, "**/*.yaml") }\n',
    "onboarding/workspaces/a/app.yaml": "name: app\n",
    "onboarding/workspaces/b/db.yaml": "name: db\n",
}


def test_a_path_module_string_names_a_folder_and_covers_every_file_under_it():
    global ONBOARDING
    saved, ONBOARDING = ONBOARDING, MODULE_ARG
    try:
        graph = _onboarding_graph()
    finally:
        ONBOARDING = saved
    refs = [e for e in graph["edges"] if e.get("via") == "terraform:path"]

    assert [(e["status"], e["to"], e["glob"], e["matched"]) for e in refs] == [
        ("verified", "file:onboarding/workspaces/a/app.yaml", "onboarding/workspaces/**", 2)]
    assert _dependents(_reverse_imports(graph), "onboarding/workspaces/b/db.yaml") == ["onboarding/main.tf"]


def test_strings_inside_file_function_calls_are_not_read_twice():
    refs = terraform.extract("m/main.tf", 'x = fileset("${path.module}/teams", "*.yml")\n')["path_refs"]

    assert [(r["via"], r["spec"]) for r in refs] == [("terraform:fileset", "teams/*.yml")]
