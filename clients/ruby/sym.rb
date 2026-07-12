# sym — the SymBridge client for Ruby.
#
#     require_relative "sym"
#     mean = Sym.call("python", "statistics.mean", [[1, 2, 3]])
#     lst  = Sym.new_obj("java", "java.util.ArrayList")
#     lst.add("from ruby")
#     puts lst.size
#
# Ruby importing Python libs. Java objects living in Ruby variables.
# The hub is a Sym stdio host this client spawns and owns.
require "json"
require "open3"

module Sym
  @stdin = @stdout = @wait = nil
  @next_id = 0

  def self.root
    return ENV["SYM_HOME"] if ENV["SYM_HOME"] && File.directory?(File.join(ENV["SYM_HOME"], "bridge"))
    home = File.join(Dir.home, ".sym")
    return home if File.directory?(File.join(home, "bridge"))
    d = File.expand_path(File.dirname(__FILE__))
    5.times do
      return d if File.directory?(File.join(d, "bridge"))
      d = File.dirname(d)
    end
    core = `python3 -c "import sym_lang, os; print(os.path.join(os.path.dirname(sym_lang.__file__), 'core'))" 2>/dev/null`.strip
    return core if !core.empty? && File.directory?(File.join(core, "bridge"))
    raise "sym: no Sym found (pip install sym-lang, or set SYM_HOME)"
  end

  def self.ensure_host
    return if @stdin
    @stdin, @stdout, @wait = Open3.popen2("python3", File.join(root, "bridge", "stdio_host.py"))
    at_exit { close }
  end

  def self.request(msg)
    ensure_host
    @next_id += 1
    msg["id"] = @next_id
    @stdin.puts(JSON.generate(msg))
    @stdin.flush
    loop do
      line = @stdout.gets or raise "sym host died"
      resp = JSON.parse(line) rescue next
      next unless resp["id"] == @next_id
      raise "sym: #{resp["error"]}" unless resp["ok"]
      return wrap(resp["value"])
    end
  end

  def self.wrap(v)
    if v.is_a?(Hash) && v["__sym__"] == "handle"
      Handle.new(v)
    elsif v.is_a?(Array)
      v.map { |x| wrap(x) }
    else
      v
    end
  end

  def self.unwrap(args)
    args.map { |a| a.is_a?(Handle) ? a.tag : a }
  end

  def self.import(lang, target) = request({ "op" => "import", "lang" => lang, "target" => target })
  def self.call(lang, target, args = []) = request({ "op" => "call", "lang" => lang, "target" => target, "args" => unwrap(args) })
  def self.new_obj(lang, target, args = []) = request({ "op" => "new", "lang" => lang, "target" => target, "args" => unwrap(args) })
  def self.block(lang, code) = request({ "op" => "exec", "lang" => lang, "code" => code })
  def self.get(key) = request({ "op" => "get", "key" => key })
  def self.set(key, value) = request({ "op" => "set", "key" => key, "value" => value })
  def self.c_call(lib_fn, args, ret = "int", argtypes = []) = request({ "op" => "call", "lang" => "c", "target" => lib_fn, "args" => args, "ret" => ret, "argtypes" => argtypes })

  def self.close
    return unless @stdin
    begin; request({ "op" => "shutdown" }); rescue; end
    begin; @stdin.close; rescue; end
    @stdin = nil
  end

  class Handle
    attr_reader :tag
    def initialize(tag); @tag = tag; end
    def method_missing(name, *args)
      Sym.request({ "op" => "hcall", "lang" => @tag["runtime"],
                    "handle" => @tag["id"], "method" => name.to_s,
                    "args" => Sym.unwrap(args) })
    end
    def respond_to_missing?(*); true; end
    def [](key)
      Sym.request({ "op" => "index", "lang" => @tag["runtime"],
                    "handle" => @tag["id"], "args" => [key] })
    end
    def to_s; "#<Sym::Handle #{@tag["runtime"]}##{@tag["id"]} #{@tag["type"]}>"; end
  end
end

# ── selftest: this consumer against every provider ──────────
if __FILE__ == $0
  checks = {
    "java"   => -> { Sym.call("java", "java.lang.Math.pow", [2, 5]) == 32.0 },
    "js"     => -> { Sym.call("js", "Math.max", [3, 9, 2]) == 9 },
    "python" => -> { Sym.call("python", "math.sqrt", [81]) == 9.0 },
    "php"    => -> { Sym.call("php", "strtoupper", ["sym"]) == "SYM" },
    "ruby"   => -> { Sym.call("ruby", "Math.sqrt", [144]) == 12.0 },
    "r"      => -> { Sym.call("r", "mean", [[1, 2, 3, 4, 5]]) == 3 },
    "perl"   => -> { Sym.import("perl", "POSIX"); Sym.call("perl", "POSIX.floor", [3.7]) == 3 },
    "c"      => -> { (Sym.c_call("m.sqrt", [2.0], "double") - 1.41421).abs < 0.001 },
  }
  checks.each { |name, fn| puts "  #{fn.call ? "✅" : "❌"} ruby → #{name}" }
  lst = Sym.new_obj("java", "java.util.ArrayList")
  lst.add("from ruby")
  puts "  #{lst.size == 1 ? "✅" : "❌"} ruby → java live object"
  Sym.close
  puts "MATRIX_ROW_OK ruby"
end
