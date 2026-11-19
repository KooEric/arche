from tax import total


def make_receipt(amount):
    return f"Total: {total(amount)}"
