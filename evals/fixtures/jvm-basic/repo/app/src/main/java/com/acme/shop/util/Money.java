package com.acme.shop.util;

import java.math.BigDecimal;

public final class Money {
    public static final String NOTE = "import com.acme.shop.model.Order;";

    public static BigDecimal cents(long value) {
        return BigDecimal.valueOf(value, 2);
    }
}

class Rounding {
}
