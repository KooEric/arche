package translator

import "strings"

type Uppercase struct{}

func (Uppercase) Translate(input string) string {
    return strings.ToUpper(input)
}
