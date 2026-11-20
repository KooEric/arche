from registry import STAGES

def run(value, config):
    for name in config["stages"]:
        value = STAGES[name](value)
    return value
