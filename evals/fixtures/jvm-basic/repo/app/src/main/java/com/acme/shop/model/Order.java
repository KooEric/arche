package com.acme.shop.model;

import java.util.List;

/** An order. Mentions of import com.acme.shop.util.Money; in comments are ignored. */
public record Order(String id, List<Line> lines) {
    public record Line(String sku, int quantity) {}

    public static Order empty(String id) {
        return new Order(id, List.of());
    }
}
