package org.springframework.samples.petclinic.api;

import org.springframework.cloud.openfeign.FeignClient;
import org.springframework.web.bind.annotation.GetMapping;

@FeignClient(name = "billing")
public interface BillingClient {

	@GetMapping("/invoices")
	String invoices();

}
