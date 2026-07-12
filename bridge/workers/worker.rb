# SymBridge Ruby worker — a Ruby runtime conducted by Sym.
# JSON lines over stdin/stdout. Talks only to Sym.
require "json"

$modules = {}
$handles = {}
$hid = 0

def handle_for(obj)
  $hid += 1
  $handles[$hid] = obj
  { "__sym__" => "handle", "runtime" => "ruby", "id" => $hid,
    "type" => obj.class.name }
end

def deref(args)
  (args || []).map do |a|
    if a.is_a?(Hash) && a["__sym__"] == "handle"
      $handles[a["id"]] or raise "stale handle ##{a['id']}"
    else a end
  end
end

BASIC = [Integer, Float, String, TrueClass, FalseClass, NilClass, Symbol]
def to_symbol(v)
  return v.map { |x| to_symbol(x) } if v.is_a?(Array)
  return v.transform_values { |x| to_symbol(x) } if v.is_a?(Hash)
  return v if BASIC.any? { |k| v.is_a?(k) }
  handle_for(v)
end

def ok(id, value = nil, exports = nil)
  r = { id: id, ok: true, value: value }
  r[:exports] = exports if exports
  STDOUT.puts(JSON.generate(r)); STDOUT.flush
end

def fail_(id, e)
  STDOUT.puts(JSON.generate({ id: id, ok: false, error: e.message,
                              trace: (e.backtrace || []).first(8).join("\n") }))
  STDOUT.flush
end

def sym_import(target)
  # gem → ./<t>.rb → ./rblib/<t>.rb → already-loaded constant
  begin
    require target
    return { gem: target }
  rescue LoadError
  end
  ["./#{target}.rb", "./rblib/#{target}.rb"].each do |f|
    if File.exist?(f)
      require_relative File.expand_path(f)
      return { file: f }
    end
  end
  return { const: target } if Object.const_defined?(target.split("::").first) rescue nil
  raise "cannot import '#{target}' (no gem, ./#{target}.rb, ./rblib/#{target}.rb)"
end

def sym_call(target, args)
  # "Module.method" / "Module::Class.method" / bare method
  parts = target.gsub("::", ".").split(".")
  meth = parts.pop
  if parts.empty?
    raise "'#{target}' is not callable" unless Object.respond_to?(meth, true)
    return Object.send(meth, *args)
  end
  obj = parts.inject(Object) { |o, c| o.const_get(c) }
  obj.send(meth, *args)
end

STDIN.each_line do |line|
  line = line.strip
  next if line.empty?
  msg = JSON.parse(line) rescue next
  id = msg["id"]
  begin
    case msg["op"]
    when "ping" then ok(id, "pong")
    when "shutdown" then ok(id, "bye"); exit 0
    when "import" then ok(id, sym_import(msg["target"]))
    when "call" then ok(id, to_symbol(sym_call(msg["target"], deref(msg["args"]))))
    when "new"
      parts = msg["target"].gsub("::", ".").split(".")
      cls = parts.inject(Object) { |o, c| o.const_get(c) }
      ok(id, handle_for(cls.new(*deref(msg["args"]))))
    when "hcall"
      obj = $handles[msg["handle"]] or raise "stale handle ##{msg['handle']}"
      ok(id, to_symbol(obj.send(msg["method"], *deref(msg["args"]))))
    when "free" then $handles.delete(msg["handle"]); ok(id, true)
    when "exec"
      sym = msg["env"] || {}
      b = binding
      b.local_variable_set(:sym, sym)
      value = b.eval(msg["code"])
      ok(id, value, sym)
    else raise "unknown op '#{msg["op"]}'"
    end
  rescue Exception => e
    fail_(id, e)
  end
end
