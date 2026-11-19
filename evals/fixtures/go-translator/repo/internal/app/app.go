package app

type Translator interface {
    Translate(string) string
}

func Run(input string, engine Translator) string {
    return translate(input, engine)
}

func translate(input string, engine Translator) string {
    if engine == nil {
        return input
    }
    return engine.Translate(input)
}
