package org.springframework.samples.petclinic.api

import org.springframework.web.bind.annotation.GetMapping
import org.springframework.web.bind.annotation.PostMapping
import org.springframework.web.bind.annotation.RequestMapping
import org.springframework.web.bind.annotation.RequestMethod
import org.springframework.web.bind.annotation.RestController

@RestController
@RequestMapping("/api/visits")
class VisitApi {

    @GetMapping
    fun all(): List<String> = emptyList()

    @PostMapping(value = ["/", "/new"])
    fun create(): String = "created"

    @GetMapping(path = arrayOf("/{id}"))
    fun one(id: Int): String = "visit $id"

    @RequestMapping("/search", method = [RequestMethod.GET, RequestMethod.POST])
    fun search(): List<String> = emptyList()
}
