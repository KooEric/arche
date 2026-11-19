from collections import Counter

def counts(tags):
    return sorted(Counter(tags).items())
