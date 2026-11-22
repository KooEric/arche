package com.acme.shop.data

import com.acme.shop.model.*
import kotlinx.coroutines.flow.Flow

interface OrderRepository {
    fun load(id: String): Order
}

internal class MemoryOrders : OrderRepository {
    override fun load(id: String): Order = Order.empty(id)
}

fun defaultRepository(): OrderRepository = MemoryOrders()

typealias Orders = List<Order>
