# sym — the SymBridge client for R.
#
#     source("sym.R")
#     sym_call("python", "math.sqrt", list(81))          # R importing Python
#     lst <- sym_new("java", "java.util.ArrayList")
#     h_call(lst, "add", list("from R"))                 # live Java object
#
# Base R has no bidirectional pipes, so this client speaks to the host
# through a pair of FIFOs — same JSON protocol, different plumbing.
suppressMessages(library(jsonlite))

.sym <- new.env()
.sym$id <- 0L
.sym$up <- FALSE

.sym_root <- function() {
  env <- Sys.getenv("SYM_HOME")
  if (nzchar(env) && dir.exists(file.path(env, "bridge"))) return(env)
  home <- file.path(Sys.getenv("HOME"), ".sym")
  if (dir.exists(file.path(home, "bridge"))) return(home)
  core <- tryCatch(system2("python3",
    c("-c", shQuote("import sym_lang, os; print(os.path.join(os.path.dirname(sym_lang.__file__), 'core'))")),
    stdout = TRUE, stderr = FALSE), error = function(e) "")
  core <- trimws(paste(core, collapse = ""))
  if (nzchar(core) && dir.exists(file.path(core, "bridge"))) return(core)
  stop("sym: no Sym found (pip install sym-lang, or set SYM_HOME)")
}

.sym_ensure <- function() {
  if (.sym$up) return(invisible())
  root <- .sym_root()
  tmp <- tempfile("symfifo")
  .sym$fin <- paste0(tmp, ".in")    # we write requests here
  .sym$fout <- paste0(tmp, ".out")  # we read responses here
  system(paste("mkfifo", shQuote(.sym$fin), shQuote(.sym$fout)))
  system(paste0("python3 ", shQuote(file.path(root, "bridge", "stdio_host.py")),
                " < ", shQuote(.sym$fin), " > ", shQuote(.sym$fout), " &"))
  .sym$wcon <- fifo(.sym$fin, open = "w", blocking = TRUE)
  .sym$rcon <- fifo(.sym$fout, open = "r", blocking = TRUE)
  .sym$up <- TRUE
  reg.finalizer(.sym, function(e) try(sym_close(), silent = TRUE), onexit = TRUE)
  invisible()
}

sym_request <- function(msg) {
  .sym_ensure()
  force(msg)  # R is lazy: nested sym calls inside `msg` must fully run
              # BEFORE we take an id, or the id sequence interleaves
  .sym$id <- .sym$id + 1L
  my_id <- .sym$id
  msg$id <- my_id
  writeLines(toJSON(msg, auto_unbox = TRUE, null = "null", digits = NA), .sym$wcon)
  flush(.sym$wcon)
  repeat {
    line <- readLines(.sym$rcon, n = 1)
    if (length(line) == 0) stop("sym host died")
    resp <- tryCatch(fromJSON(line, simplifyVector = FALSE), error = function(e) NULL)
    if (is.null(resp) || !identical(resp$id, my_id)) next
    if (!isTRUE(resp$ok)) stop(paste("sym:", resp$error))
    return(.sym_wrap(resp$value))
  }
}

.sym_wrap <- function(v) {
  if (is.list(v) && identical(v[["__sym__"]], "handle")) {
    class(v) <- "sym_handle"
    return(v)
  }
  if (is.list(v)) return(lapply(v, .sym_wrap))
  v
}

.sym_unwrap <- function(args) {
  lapply(args, function(a) {
    if (inherits(a, "sym_handle")) unclass(a) else a
  })
}

sym_import <- function(lang, target) sym_request(list(op = "import", lang = lang, target = target))
sym_call   <- function(lang, target, args = list()) sym_request(list(op = "call", lang = lang, target = target, args = I(.sym_unwrap(args))))
sym_new    <- function(lang, target, args = list()) sym_request(list(op = "new", lang = lang, target = target, args = I(.sym_unwrap(args))))
sym_block  <- function(lang, code) sym_request(list(op = "exec", lang = lang, code = code))
sym_c_call <- function(lib_fn, args, ret = "int", argtypes = list())
  sym_request(list(op = "call", lang = "c", target = lib_fn, args = I(args), ret = ret, argtypes = I(argtypes)))
h_call     <- function(h, method, args = list())
  sym_request(list(op = "hcall", lang = h$runtime, handle = h$id, method = method, args = I(.sym_unwrap(args))))
sym_close  <- function() {
  if (!.sym$up) return(invisible())
  try(sym_request(list(op = "shutdown")), silent = TRUE)
  try(close(.sym$wcon), silent = TRUE)
  try(close(.sym$rcon), silent = TRUE)
  unlink(c(.sym$fin, .sym$fout))
  .sym$up <- FALSE
  invisible()
}

# ── selftest ─────────────────────────────────────────────────
if (sys.nframe() == 0 && !interactive()) {
  ok <- function(b, name) cat(sprintf("  %s r \u2192 %s\n", if (isTRUE(b)) "\u2705" else "\u274c", name))
  ok(sym_call("java", "java.lang.Math.pow", list(2, 5)) == 32, "java")
  ok(sym_call("js", "Math.max", list(3, 9, 2)) == 9, "js")
  ok(sym_call("python", "math.sqrt", list(81)) == 9, "python")
  ok(sym_call("php", "strtoupper", list("sym")) == "SYM", "php")
  ok(sym_call("ruby", "Math.sqrt", list(144)) == 12, "ruby")
  ok(sym_call("r", "mean", list(c(1, 2, 3, 4, 5))) == 3, "r")
  sym_import("perl", "POSIX")
  ok(sym_call("perl", "POSIX.floor", list(3.7)) == 3, "perl")
  ok(abs(sym_c_call("m.sqrt", list(2.0), "double", list("double")) - 1.41421) < 0.001, "c")
  lst <- sym_new("java", "java.util.ArrayList")
  h_call(lst, "add", list("from R"))
  ok(h_call(lst, "size") == 1, "java live object")
  sym_close()
  cat("MATRIX_ROW_OK r\n")
}
