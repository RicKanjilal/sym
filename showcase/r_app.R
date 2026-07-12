# An R program borrowing Python, Java, PHP, Ruby, C.
source(file.path(Sys.getenv("SYM_HOME"), "clients", "r", "sym.R"))

sales <- c(984, 1250, 872, 1490, 1105)
mean_py <- sym_call("python", "statistics.mean", list(sales))          # Python
uuid    <- h_call(sym_call("java", "java.util.UUID.randomUUID"), "toString")  # Java
fmt     <- sym_call("php", "number_format", list(mean_py, 2))          # PHP
up      <- sym_call("ruby", "Kernel.format", list("sales report %s", substr(uuid, 1, 8)))  # Ruby
root    <- sym_c_call("m.sqrt", list(mean_py), "double", list("double"))  # C

cat(toupper(up), "\n")
cat(sprintf("  mean       = %s   (Python via R)\n", fmt))
cat(sprintf("  sqrt(mean) = %.3f     (C libm)\n", root))
cat("R importing Python, Java, PHP, Ruby, C: OK\n")
sym_close()
