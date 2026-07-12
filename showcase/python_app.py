# A PYTHON program borrowing Java's Lucene, R's statistics, Ruby, PHP, C.
import sym

sales = [984, 1250, 872, 1490, 1105]

sd     = sym.call("r", "sd", [sales])                        # R
uuid   = sym.java("java.util.UUID").randomUUID().toString()  # Java
pretty = sym.call("php", "number_format", [sum(sales) / len(sales), 2])  # PHP
shout  = sym.call("ruby", "String#upcase") if False else \
         sym.call("ruby", "Kernel.format", ["sales report %s", uuid[:8]])  # Ruby
root   = sym.c("m").call("sqrt", [sum(sales) / len(sales)], "double")     # C

print(shout.upper())
print(f"  mean       = {pretty}   (PHP formatting)")
print(f"  sd         = {sd:.2f}     (R)")
print(f"  sqrt(mean) = {root:.3f}     (C libm)")
print("PYTHON importing R, Java, PHP, Ruby, C: OK")
sym.close()
