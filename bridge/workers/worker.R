# SymBridge R worker — an R runtime conducted by Sym.
# JSON lines over stdin/stdout. Talks only to Sym.
suppressMessages(library(jsonlite))

con_in <- file("stdin", open = "r", blocking = TRUE)

respond <- function(x) {
  cat(toJSON(x, auto_unbox = TRUE, null = "null", digits = NA), "\n", sep = "")
  flush(stdout())
}
ok <- function(id, value = NULL, exports = NULL) {
  r <- list(id = id, ok = TRUE, value = value)
  if (!is.null(exports)) r$exports <- exports
  respond(r)
}
fail <- function(id, msg) respond(list(id = id, ok = FALSE, error = msg, trace = ""))

to_r <- function(x) {
  # JSON array of scalars -> R vector; nested/named stays list
  if (is.list(x)) {
    if (length(x) > 0 && is.null(names(x)) &&
        all(vapply(x, function(e) is.atomic(e) && length(e) == 1, TRUE)))
      return(unlist(x))
    return(lapply(x, to_r))
  }
  x
}

sym_import <- function(target) {
  # CRAN/library → ./<t>.R → ./rlib/<t>.R
  if (suppressWarnings(require(target, character.only = TRUE, quietly = TRUE)))
    return(list(package = target))
  for (f in c(paste0("./", target, ".R"), paste0("./rlib/", target, ".R"))) {
    if (file.exists(f)) { source(f); return(list(file = f)) }
  }
  stop(paste0("cannot import '", target, "' (no package, ./", target, ".R, ./rlib/)"))
}

sym_call <- function(target, args) {
  # "pkg::fn" or "fn" — R functions take positional args
  fn <- eval(parse(text = gsub("\\.", "::", target, fixed = FALSE)))
  if (!is.function(fn)) fn <- get(target)
  do.call(fn, args)
}

HANDLES <- new.env()
HID <- 0L

handle_for <- function(obj) {
  HID <<- HID + 1L
  assign(as.character(HID), obj, envir = HANDLES)
  list("__sym__" = "handle", runtime = "r", id = HID,
       type = paste(class(obj), collapse = ","))
}
deref_args <- function(args) {
  lapply(args, function(a) {
    if (is.list(a) && identical(a[["__sym__"]], "handle"))
      get(as.character(a$id), envir = HANDLES)
    else to_r(a)
  })
}
to_symbol <- function(v) {
  if (is.null(v) || is.atomic(v)) return(v)
  if (is.list(v) && is.null(attr(v, "class"))) return(lapply(v, to_symbol))
  handle_for(v)  # models, environments, S4 objects -> handles
}

env_sym <- new.env()

while (TRUE) {
  line <- readLines(con_in, n = 1)
  if (length(line) == 0) break
  line <- trimws(line)
  if (nchar(line) == 0) next
  msg <- tryCatch(fromJSON(line, simplifyVector = FALSE), error = function(e) NULL)
  if (is.null(msg)) next
  id <- msg$id
  result <- tryCatch({
    if (msg$op == "ping") ok(id, "pong")
    else if (msg$op == "shutdown") { ok(id, "bye"); quit(save = "no") }
    else if (msg$op == "import") ok(id, sym_import(msg$target))
    else if (msg$op == "call") {
      args <- if (is.null(msg$args)) list() else deref_args(msg$args)
      ok(id, to_symbol(sym_call(msg$target, args)))
    }
    else if (msg$op == "hcall") {
      obj <- get(as.character(msg$handle), envir = HANDLES)
      args <- if (is.null(msg$args)) list() else deref_args(msg$args)
      fn <- get(msg$method)
      ok(id, to_symbol(do.call(fn, c(list(obj), args))))
    }
    else if (msg$op == "free") {
      rm(list = as.character(msg$handle), envir = HANDLES); ok(id, TRUE)
    }
    else if (msg$op == "exec") {
      sym <- if (is.null(msg$env)) list() else lapply(msg$env, to_r)
      assign("sym", sym, envir = env_sym)
      value <- eval(parse(text = msg$code), envir = env_sym)
      exports <- lapply(get("sym", envir = env_sym), to_symbol)
      ok(id, to_symbol(value), exports)
    }
    else fail(id, paste0("unknown op '", msg$op, "'"))
  }, error = function(e) fail(id, conditionMessage(e)))
}
