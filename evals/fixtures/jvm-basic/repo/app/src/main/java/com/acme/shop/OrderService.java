package com.acme.shop;

import static com.acme.shop.util.Money.cents;

import com.acme.shop.data.OrderRepository;
import com.acme.shop.model.Order;
import com.acme.shop.model.Order.Line;
import com.acme.shop.model.Missing;
import org.slf4j.Logger;

public class OrderService {
    private final OrderRepository repository;
    private Logger log;

    public OrderService(OrderRepository repository) {
        this.repository = repository;
    }

    public Order find(String id) {
        Line first = null;
        Receipt receipt = new Receipt();
        return repository.load(id);
    }
}
