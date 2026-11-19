package app

import "testing"

type stub struct{}

func (stub) Translate(input string) string { return "translated:" + input }

func TestRunBranches(t *testing.T) {
    input := "original text"
    if got := Run(input, nil); got != input {
        t.Fatalf("nil translator: got %q, want original %q", got, input)
    }
    if got := Run(input, stub{}); got != "translated:"+input {
        t.Fatalf("translator: got %q", got)
    }
}
