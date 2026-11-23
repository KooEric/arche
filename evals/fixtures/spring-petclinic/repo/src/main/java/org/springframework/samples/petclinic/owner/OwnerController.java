package org.springframework.samples.petclinic.owner;

import org.springframework.stereotype.Controller;
import org.springframework.ui.Model;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.ModelAttribute;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;

@Controller
class OwnerController {

	private static final String VIEW_OWNER_FORM = "owners/createOrUpdateOwnerForm";

	private final OwnerRepository owners;

	OwnerController(OwnerRepository owners) {
		this.owners = owners;
	}

	@ModelAttribute("owner")
	public Owner findOwner(@PathVariable(name = "ownerId", required = false) Integer ownerId) {
		return ownerId == null ? new Owner() : this.owners.findById(ownerId);
	}

	@GetMapping("/owners/new")
	public String initCreationForm() {
		return VIEW_OWNER_FORM;
	}

	@PostMapping("/owners/new")
	public String processCreationForm(Owner owner) {
		this.owners.save(owner);
		return "redirect:/owners/" + owner.getId();
	}

	@GetMapping("/owners/find")
	public String initFindForm() {
		return "owners/findOwners";
	}

	@GetMapping("/owners")
	public String processFindForm(Owner owner, Model model) {
		model.addAttribute("listOwners", this.owners.findByLastName(owner.getLastName()));
		return "owners/ownersList";
	}

	@GetMapping("/owners/{ownerId}/edit")
	public String initUpdateOwnerForm() {
		return VIEW_OWNER_FORM;
	}

	@PostMapping("/owners/{ownerId}/edit")
	public String processUpdateOwnerForm(Owner owner, @PathVariable("ownerId") int ownerId) {
		this.owners.save(owner);
		return "redirect:/owners/{ownerId}";
	}

	@GetMapping("/owners/{ownerId}")
	public String showOwner(@PathVariable("ownerId") int ownerId, Model model) {
		model.addAttribute(this.owners.findById(ownerId));
		return "owners/ownerDetails";
	}

}
