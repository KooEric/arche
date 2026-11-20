def render(ready, permitted, items):
    if ready and permitted and items:
        return "Show checkout"
    return "Hide checkout"
