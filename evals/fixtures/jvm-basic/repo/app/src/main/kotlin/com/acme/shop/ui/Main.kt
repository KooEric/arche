package com.acme.shop.ui

import com.acme.shop.OrderService
import com.acme.shop.data.defaultRepository
import com.acme.shop.model.Status as OrderStatus
import com.acme.shop.util.Money

fun main() {
    val service = OrderService(defaultRepository())
    println("${Money.cents(100)} ${OrderStatus.OPEN}")
    Screen().show(service)
}

fun String.shout(): String = uppercase()
