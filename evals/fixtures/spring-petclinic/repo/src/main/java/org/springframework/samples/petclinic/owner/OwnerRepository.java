package org.springframework.samples.petclinic.owner;

import java.util.List;

public interface OwnerRepository {

	List<Owner> findByLastName(String lastName);

	Owner findById(Integer id);

	void save(Owner owner);

}
