# A RUBY program borrowing Python, R, Java, PHP, C.
require_relative "../clients/ruby/sym"

sales = [984, 1250, 872, 1490, 1105]
mean = Sym.call("python", "statistics.mean", [sales])            # Python
sd   = Sym.call("r", "sd", [sales])                              # R
uuid = Sym.call("java", "java.util.UUID.randomUUID").toString    # Java (handle)
fmt  = Sym.call("php", "number_format", [mean, 2])               # PHP
root = Sym.c_call("m.sqrt", [mean], "double", ["double"])        # C

puts "SALES REPORT #{uuid[0, 8]}"
puts "  mean       = #{fmt}   (Python via Ruby)"
puts "  sd         = #{sd.round(2)}     (R)"
puts "  sqrt(mean) = #{root.round(3)}     (C libm)"
puts "RUBY importing Python, R, Java, PHP, C: OK"
Sym.close
