package org.springframework.samples.petclinic.owner;

import org.springframework.stereotype.Controller;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.InitBinder;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestMapping;

@Controller
@RequestMapping("/owners/{ownerId}")
class PetController {

	private static final String VIEW_PET_FORM = "pets/createOrUpdatePetForm";

	@InitBinder("owner")
	public void initOwnerBinder() {
	}

	@GetMapping("/pets/new")
	public String initCreationForm() {
		return VIEW_PET_FORM;
	}

	@PostMapping("/pets/new")
	public String processCreationForm() {
		return "redirect:/owners/{ownerId}";
	}

	@GetMapping("/pets/{petId}/edit")
	public String initUpdateForm() {
		return VIEW_PET_FORM;
	}

	@PostMapping("/pets/{petId}/edit")
	public String processUpdateForm() {
		return "redirect:/owners/{ownerId}";
	}

}
