<?php // A PHP program borrowing Python, R, Java, Ruby, C.
require __DIR__ . "/../clients/php/sym.php";

$sales = [984, 1250, 872, 1490, 1105];
$mean = Sym::call("python", "statistics.mean", [$sales]);           // Python
$sd   = Sym::call("r", "sd", [$sales]);                             // R
$uuid = Sym::call("java", "java.util.UUID.randomUUID")->toString(); // Java
$up   = Sym::call("ruby", "Kernel.format", ["sales report %s", substr($uuid, 0, 8)]); // Ruby
$root = Sym::cCall("m.sqrt", [$mean], "double", ["double"]);        // C

echo strtoupper($up), "\n";
echo "  mean       = ", number_format($mean, 2), "   (Python via PHP)\n";
echo "  sd         = ", round($sd, 2), "     (R)\n";
echo "  sqrt(mean) = ", round($root, 3), "     (C libm)\n";
echo "PHP importing Python, R, Java, Ruby, C: OK\n";
Sym::close();
