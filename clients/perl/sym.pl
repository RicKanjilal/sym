#!/usr/bin/perl
# sym — the SymBridge client for Perl.
#     require "sym.pl";
#     print Sym::call("python", "math.sqrt", [81]);   # Perl importing Python
package Sym;
use strict; use warnings;
use JSON::PP;
use IPC::Open2;
use File::Basename qw(dirname);

my $json = JSON::PP->new->utf8->allow_nonref;
my ($OUT, $IN, $PID);
my $ID = 0;

sub root {
    return $ENV{SYM_HOME} if $ENV{SYM_HOME} && -d "$ENV{SYM_HOME}/bridge";
    my $home = "$ENV{HOME}/.sym";
    return $home if -d "$home/bridge";
    my $d = dirname(__FILE__);
    for (1..5) {
        return $d if -d "$d/bridge";
        $d = dirname($d);
    }
    my $core = `python3 -c "import sym_lang, os; print(os.path.join(os.path.dirname(sym_lang.__file__), 'core'))" 2>/dev/null`;
    chomp $core;
    return $core if $core && -d "$core/bridge";
    die "sym: no Sym found (pip install sym-lang, or set SYM_HOME)\n";
}

sub ensure {
    return if $PID;
    my $r = root();
    $PID = open2($IN, $OUT, "python3", "$r/bridge/stdio_host.py");
}

sub request {
    my ($msg) = @_;
    ensure();
    $msg->{id} = ++$ID;
    print $OUT $json->encode($msg), "\n";
    $OUT->flush;
    while (my $line = <$IN>) {
        my $resp = eval { $json->decode($line) } or next;
        next unless ($resp->{id} // -1) == $ID;
        die "sym: $resp->{error}\n" unless $resp->{ok};
        return wrap($resp->{value});
    }
    die "sym host died\n";
}

sub wrap {
    my ($v) = @_;
    if (ref($v) eq "HASH" && ($v->{"__sym__"} // "") eq "handle") {
        return Sym::Handle->new($v);
    }
    return [map { wrap($_) } @$v] if ref($v) eq "ARRAY";
    return $v;
}

sub unwrap { [map { ref($_) eq "Sym::Handle" ? $_->{tag} : $_ } @{$_[0] // []}] }

sub simport { request({ op => "import", lang => $_[0], target => $_[1] }) }
sub call    { request({ op => "call", lang => $_[0], target => $_[1], args => unwrap($_[2]) }) }
sub new_obj { request({ op => "new", lang => $_[0], target => $_[1], args => unwrap($_[2]) }) }
sub block   { request({ op => "exec", lang => $_[0], code => $_[1] }) }
sub c_call  { request({ op => "call", lang => "c", target => $_[0], args => $_[1], ret => $_[2] // "int", argtypes => $_[3] // [] }) }
sub close_  { eval { request({ op => "shutdown" }) }; }

package Sym::Handle;
our $AUTOLOAD;
sub new { my ($c, $tag) = @_; bless { tag => $tag }, $c }
sub AUTOLOAD {
    my $self = shift;
    (my $m = $AUTOLOAD) =~ s/.*:://;
    return if $m eq "DESTROY";
    Sym::request({ op => "hcall", lang => $self->{tag}{runtime},
                   handle => $self->{tag}{id}, method => $m,
                   args => Sym::unwrap(\@_) });
}

package main;
unless (caller) {
    my @checks = (
        ["java",   sub { Sym::call("java", "java.lang.Math.pow", [2, 5]) == 32 }],
        ["js",     sub { Sym::call("js", "Math.max", [3, 9, 2]) == 9 }],
        ["python", sub { Sym::call("python", "math.sqrt", [81]) == 9 }],
        ["php",    sub { Sym::call("php", "strtoupper", ["sym"]) eq "SYM" }],
        ["ruby",   sub { Sym::call("ruby", "Math.sqrt", [144]) == 12 }],
        ["r",      sub { Sym::call("r", "mean", [[1, 2, 3, 4, 5]]) == 3 }],
        ["perl",   sub { Sym::simport("perl", "POSIX"); Sym::call("perl", "POSIX.floor", [3.7]) == 3 }],
        ["c",      sub { abs(Sym::c_call("m.sqrt", [2.0], "double", ["double"]) - 1.41421) < 0.001 }],
    );
    for my $c (@checks) {
        print "  ", ($c->[1]->() ? "\x{2705}" : "\x{274c}"), " perl \x{2192} $c->[0]\n";
    }
    my $lst = Sym::new_obj("java", "java.util.ArrayList");
    $lst->add("from perl");
    print "  ", ($lst->size() == 1 ? "\x{2705}" : "\x{274c}"), " perl \x{2192} java live object\n";
    Sym::close_();
    print "MATRIX_ROW_OK perl\n";
}
1;
