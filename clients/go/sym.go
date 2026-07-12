// sym — the SymBridge client for GO.
//
//	sym := NewSym()
//	v, _ := sym.Call("python", "math.sqrt", 81)   // Go importing Python
//	lst, _ := sym.NewObj("java", "java.util.ArrayList")
//	lst.Call("add", "from go")                    // live Java object in Go
//
// Go can't be a library PROVIDER (no runtime to host) — but as a CONSUMER
// it gets every ecosystem Sym hosts.
package main

import (
	"bufio"
	"strings"
	"encoding/json"
	"fmt"
	"math"
	"os"
	"os/exec"
	"path/filepath"
)

type Sym struct {
	proc *exec.Cmd
	in   *bufio.Scanner
	out  *bufio.Writer
	id   int
}

type Handle struct {
	sym *Sym
	Tag map[string]interface{}
}

func findRoot() string {
	if env := os.Getenv("SYM_HOME"); env != "" {
		if st, err := os.Stat(filepath.Join(env, "bridge")); err == nil && st.IsDir() {
			return env
		}
	}
	home, _ := os.UserHomeDir()
	p := filepath.Join(home, ".sym")
	if st, err := os.Stat(filepath.Join(p, "bridge")); err == nil && st.IsDir() {
		return p
	}
	if out, err := exec.Command("python3", "-c",
		"import sym_lang, os; print(os.path.join(os.path.dirname(sym_lang.__file__), 'core'))").Output(); err == nil {
		core := strings.TrimSpace(string(out))
		if st, err := os.Stat(filepath.Join(core, "bridge")); err == nil && st.IsDir() {
			return core
		}
	}
	panic("sym: no Sym found (pip install sym-lang, or set SYM_HOME)")
}

func NewSym() *Sym {
	root := findRoot()
	cmd := exec.Command("python3", filepath.Join(root, "bridge", "stdio_host.py"))
	stdin, _ := cmd.StdinPipe()
	stdout, _ := cmd.StdoutPipe()
	cmd.Stderr = os.Stderr
	if err := cmd.Start(); err != nil {
		panic(err)
	}
	sc := bufio.NewScanner(stdout)
	sc.Buffer(make([]byte, 1024*1024), 16*1024*1024)
	return &Sym{proc: cmd, in: sc, out: bufio.NewWriter(stdin.(interface {
		Write([]byte) (int, error)
	}))}
}

func (s *Sym) request(msg map[string]interface{}) (interface{}, error) {
	s.id++
	msg["id"] = s.id
	b, _ := json.Marshal(msg)
	s.out.Write(append(b, '\n'))
	s.out.Flush()
	for s.in.Scan() {
		var resp map[string]interface{}
		if err := json.Unmarshal(s.in.Bytes(), &resp); err != nil {
			continue
		}
		if id, ok := resp["id"].(float64); !ok || int(id) != s.id {
			continue
		}
		if ok, _ := resp["ok"].(bool); !ok {
			return nil, fmt.Errorf("sym: %v", resp["error"])
		}
		return s.wrap(resp["value"]), nil
	}
	return nil, fmt.Errorf("sym host died")
}

func (s *Sym) wrap(v interface{}) interface{} {
	if m, ok := v.(map[string]interface{}); ok && m["__sym__"] == "handle" {
		return &Handle{sym: s, Tag: m}
	}
	return v
}

func unwrap(args []interface{}) []interface{} {
	out := make([]interface{}, len(args))
	for i, a := range args {
		if h, ok := a.(*Handle); ok {
			out[i] = h.Tag
		} else {
			out[i] = a
		}
	}
	return out
}

func (s *Sym) Import(lang, target string) (interface{}, error) {
	return s.request(map[string]interface{}{"op": "import", "lang": lang, "target": target})
}
func (s *Sym) Call(lang, target string, args ...interface{}) (interface{}, error) {
	return s.request(map[string]interface{}{"op": "call", "lang": lang, "target": target, "args": unwrap(args)})
}
func (s *Sym) NewObj(lang, target string, args ...interface{}) (*Handle, error) {
	v, err := s.request(map[string]interface{}{"op": "new", "lang": lang, "target": target, "args": unwrap(args)})
	if err != nil {
		return nil, err
	}
	return v.(*Handle), nil
}
func (s *Sym) CCall(libFn, ret string, argtypes []string, args ...interface{}) (interface{}, error) {
	return s.request(map[string]interface{}{"op": "call", "lang": "c", "target": libFn,
		"args": unwrap(args), "ret": ret, "argtypes": argtypes})
}
func (s *Sym) Close() {
	s.request(map[string]interface{}{"op": "shutdown", "lang": ""})
	s.proc.Process.Kill()
}
func (h *Handle) Call(method string, args ...interface{}) (interface{}, error) {
	return h.sym.request(map[string]interface{}{"op": "hcall", "lang": h.Tag["runtime"],
		"handle": h.Tag["id"], "method": method, "args": unwrap(args)})
}

// ── selftest ─────────────────────────────────────────────────
func main() {
	sym := NewSym()
	check := func(name string, got interface{}, want float64) {
		n, _ := got.(float64)
		mark := "\u2705"
		if math.Abs(n-want) > 0.01 {
			mark = "\u274c"
		}
		fmt.Printf("  %s go \u2192 %s\n", mark, name)
	}
	v, _ := sym.Call("java", "java.lang.Math.pow", 2, 5)
	check("java", v, 32)
	v, _ = sym.Call("js", "Math.max", 3, 9, 2)
	check("js", v, 9)
	v, _ = sym.Call("python", "math.sqrt", 81)
	check("python", v, 9)
	sv, _ := sym.Call("php", "strtoupper", "sym")
	fmt.Printf("  %s go \u2192 php\n", map[bool]string{true: "\u2705", false: "\u274c"}[sv == "SYM"])
	v, _ = sym.Call("ruby", "Math.sqrt", 144)
	check("ruby", v, 12)
	v, _ = sym.Call("r", "mean", []interface{}{1, 2, 3, 4, 5})
	check("r", v, 3)
	sym.Import("perl", "POSIX")
	v, _ = sym.Call("perl", "POSIX.floor", 3.7)
	check("perl", v, 3)
	v, _ = sym.CCall("m.sqrt", "double", []string{"double"}, 2.0)
	check("c", v, 1.41421)
	lst, _ := sym.NewObj("java", "java.util.ArrayList")
	lst.Call("add", "from go")
	sz, _ := lst.Call("size")
	check("java live object", sz, 1)
	sym.Close()
	fmt.Println("MATRIX_ROW_OK go")
}
