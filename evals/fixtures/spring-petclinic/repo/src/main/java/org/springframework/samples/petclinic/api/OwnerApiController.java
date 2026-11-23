package org.springframework.samples.petclinic.api;

import org.springframework.web.bind.annotation.DeleteMapping;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PatchMapping;
import org.springframework.web.bind.annotation.PutMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestMethod;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping(path = "/api/v1", produces = "application/json")
public class OwnerApiController {

	@GetMapping
	public String index() {
		return "{}";
	}

	@RequestMapping(value = "/owners", method = RequestMethod.GET)
	public String listOwners() {
		return "[]";
	}

	@RequestMapping(value = { "/owners/{id}", "/owner/{id}" },
			method = { RequestMethod.PUT, RequestMethod.PATCH })
	public String updateOwner() {
		return "{}";
	}

	@DeleteMapping(path = "/owners/{id}")
	public void deleteOwner() {
	}

	@PutMapping(value = "/owners/{id}/address", consumes = "application/json")
	public void replaceAddress() {
	}

	@PatchMapping("owners/{id}/phone")
	public void patchPhone() {
	}

	@org.springframework.web.bind.annotation.RequestMapping("/ping")
	public String ping() {
		return "pong";
	}

	@GetMapping(Paths.REPORT)
	public String report() {
		return "report";
	}

	String route = "@GetMapping(\"/in-a-string\")";

}
