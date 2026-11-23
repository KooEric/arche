# spring-petclinic

A small Spring MVC repository shaped like spring-petclinic, used to check that
arche reads Spring HTTP routes: class-level `@RequestMapping` prefixes, the
method-level `@GetMapping`/`@PostMapping`/`@PutMapping`/`@DeleteMapping`/
`@PatchMapping` shortcuts, `@RequestMapping(method = ...)`, `value =`/`path =`
and array forms, in Java and Kotlin. A Feign client, a commented-out mapping and
a mapping whose path is a constant must not become routes.

The code is written for this fixture; it is not copied from spring-petclinic.
There is no `expected_graph.json`, so import scoring leaves it out; the routes
are checked by `tests/unit/test_jvm_http.py`.
