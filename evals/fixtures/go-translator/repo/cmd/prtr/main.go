package main

import (
    "fmt"
    "os"

    application "example.test/translator/internal/app"
    "example.test/translator/internal/translator"
)

func main() {
    var engine application.Translator
    if os.Getenv("TRANSLATOR_ENABLED") == "1" {
        engine = translator.Uppercase{}
    }
    fmt.Print(application.Run(os.Args[1], engine))
}
