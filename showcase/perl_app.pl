#!/usr/bin/perl # A PERL program borrowing Python, R, Java, PHP, C.
use strict; use warnings;
use FindBin;
require "$FindBin::Bin/../clients/perl/sym.pl";

my $sales = [984, 1250, 872, 1490, 1105];
my $mean = Sym::call("python", "statistics.mean", [$sales]);          # Python
my $sd   = Sym::call("r", "sd", [$sales]);                            # R
my $uuid = Sym::call("java", "java.util.UUID.randomUUID")->toString;  # Java
my $fmt  = Sym::call("php", "number_format", [$mean, 2]);             # PHP
my $root = Sym::c_call("m.sqrt", [$mean], "double", ["double"]);      # C

printf "SALES REPORT %s\n", substr($uuid, 0, 8);
printf "  mean       = %s   (Python via Perl)\n", $fmt;
printf "  sd         = %.2f     (R)\n", $sd;
printf "  sqrt(mean) = %.3f     (C libm)\n", $root;
print "PERL importing Python, R, Java, PHP, C: OK\n";
Sym::close_();
