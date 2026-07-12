package main

// A GO program borrowing Python's statistics, R's sd, Java, PHP, C.
// Build: run_all.sh copies the client's functions in via go_build dir.

import (
	"fmt"
	"os"
)

func mainApp(sym *Sym) {
	sales := []interface{}{984, 1250, 872, 1490, 1105}
	mean, _ := sym.Call("python", "statistics.mean", sales)          // Python
	sd, _ := sym.Call("r", "sd", sales)                              // R
	u, _ := sym.Call("java", "java.util.UUID.randomUUID")            // Java
	id, _ := u.(*Handle).Call("toString")
	f, _ := sym.Call("php", "number_format", mean, 2)                // PHP
	root, _ := sym.CCall("m.sqrt", "double", []string{"double"}, mean) // C

	fmt.Printf("SALES REPORT %s\n", id.(string)[:8])
	fmt.Printf("  mean       = %s   (Python via Go)\n", f)
	fmt.Printf("  sd         = %.2f     (R)\n", sd.(float64))
	fmt.Printf("  sqrt(mean) = %.3f     (C libm)\n", root.(float64))
	fmt.Println("GO importing Python, R, Java, PHP, C: OK")
	sym.Close()
	os.Exit(0)
}
