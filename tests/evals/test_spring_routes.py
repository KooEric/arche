"""Spring routes in a spring-petclinic-shaped repository reach the graph with a source line."""
import score

PKG = "src/main/java/org/springframework/samples/petclinic/"
KT = "src/main/kotlin/org/springframework/samples/petclinic/"

EXPECTED = {
    ("GET", "/", PKG + "system/WelcomeController.java", 9),
    ("GET", "/oups", PKG + "system/CrashController.java", 10),
    ("GET", "/owners/new", PKG + "owner/OwnerController.java", 26),
    ("POST", "/owners/new", PKG + "owner/OwnerController.java", 31),
    ("GET", "/owners/find", PKG + "owner/OwnerController.java", 37),
    ("GET", "/owners", PKG + "owner/OwnerController.java", 42),
    ("GET", "/owners/{ownerId}/edit", PKG + "owner/OwnerController.java", 48),
    ("POST", "/owners/{ownerId}/edit", PKG + "owner/OwnerController.java", 53),
    ("GET", "/owners/{ownerId}", PKG + "owner/OwnerController.java", 59),
    ("GET", "/owners/{ownerId}/pets/new", PKG + "owner/PetController.java", 19),
    ("POST", "/owners/{ownerId}/pets/new", PKG + "owner/PetController.java", 24),
    ("GET", "/owners/{ownerId}/pets/{petId}/edit", PKG + "owner/PetController.java", 29),
    ("POST", "/owners/{ownerId}/pets/{petId}/edit", PKG + "owner/PetController.java", 34),
    ("GET", "/owners/{ownerId}/pets/{petId}/visits/new", PKG + "owner/VisitController.java", 10),
    ("POST", "/owners/{ownerId}/pets/{petId}/visits/new", PKG + "owner/VisitController.java", 15),
    ("GET", "/vets.html", PKG + "vet/VetController.java", 10),
    ("GET", "/vets", PKG + "vet/VetController.java", 15),
    ("GET", "/api/v1", PKG + "api/OwnerApiController.java", 15),
    ("GET", "/api/v1/owners", PKG + "api/OwnerApiController.java", 20),
    ("PUT", "/api/v1/owners/{id}", PKG + "api/OwnerApiController.java", 25),
    ("PATCH", "/api/v1/owners/{id}", PKG + "api/OwnerApiController.java", 25),
    ("PUT", "/api/v1/owner/{id}", PKG + "api/OwnerApiController.java", 25),
    ("PATCH", "/api/v1/owner/{id}", PKG + "api/OwnerApiController.java", 25),
    ("DELETE", "/api/v1/owners/{id}", PKG + "api/OwnerApiController.java", 31),
    ("PUT", "/api/v1/owners/{id}/address", PKG + "api/OwnerApiController.java", 35),
    ("PATCH", "/api/v1/owners/{id}/phone", PKG + "api/OwnerApiController.java", 39),
    ("ALL", "/api/v1/ping", PKG + "api/OwnerApiController.java", 43),
    ("GET", "/api/visits", KT + "api/VisitApi.kt", 13),
    ("POST", "/api/visits", KT + "api/VisitApi.kt", 16),
    ("POST", "/api/visits/new", KT + "api/VisitApi.kt", 16),
    ("GET", "/api/visits/{id}", KT + "api/VisitApi.kt", 19),
    ("GET", "/api/visits/search", KT + "api/VisitApi.kt", 22),
    ("POST", "/api/visits/search", KT + "api/VisitApi.kt", 22),
}


def _routes():
    return score.run_pipeline(score.FIXTURES_DIR / "spring-petclinic" / "repo")["http_routes"]


def test_every_spring_mapping_becomes_a_route_with_its_source_line():
    found = {(r["method"], r["path"], r["file"], r["line"]) for r in _routes()}

    assert found == EXPECTED


def test_every_route_cites_the_line_holding_its_mapping_annotation():
    root = score.FIXTURES_DIR / "spring-petclinic" / "repo"
    for route in _routes():
        line = (root / route["file"]).read_text(encoding="utf-8").splitlines()[route["line"] - 1]
        assert "Mapping" in line, route
        assert route["handler_line"] > route["line"], route
        assert not route.get("prefix_unknown") and route["dynamic"] is False
