#!/usr/bin/perl
# SymBridge Perl worker — a Perl runtime conducted by Sym.
use strict; use warnings;
use JSON::PP;

my $json = JSON::PP->new->utf8->allow_nonref;
$| = 1;

sub respond { print $json->encode($_[0]), "\n"; }
sub ok_ {
    my ($id, $value, $exports) = @_;
    my %r = (id => $id, ok => JSON::PP::true, value => $value);
    $r{exports} = $exports if defined $exports;
    respond(\%r);
}
sub fail_ { respond({ id => $_[0], ok => JSON::PP::false, error => "$_[1]", trace => "" }); }

our %SYM; our %sym;
our %HANDLES; our $HID = 0;

sub handle_for {
    my ($obj) = @_;
    $HANDLES{++$HID} = $obj;
    return { "__sym__" => "handle", "runtime" => "perl", "id" => $HID,
             "type" => ref($obj) };
}
sub deref {
    my @out;
    for my $a (@{ $_[0] // [] }) {
        if (ref($a) eq "HASH" && ($a->{"__sym__"} // "") eq "handle") {
            my $o = $HANDLES{$a->{id}} or die "stale handle #$a->{id}\n";
            push @out, $o;
        } else { push @out, $a }
    }
    return @out;
}
sub to_symbol {
    my ($v) = @_;
    my $r = ref($v);
    return $v unless $r;
    return [map { to_symbol($_) } @$v] if $r eq "ARRAY";
    return { map { $_ => to_symbol($v->{$_}) } keys %$v } if $r eq "HASH";
    return handle_for($v);  # blessed ref -> handle
}

while (my $line = <STDIN>) {
    chomp $line;
    next unless length $line;
    my $msg = eval { $json->decode($line) } or next;
    my ($id, $op) = ($msg->{id}, $msg->{op} // "");
    eval {
        if ($op eq "ping") { ok_($id, "pong") }
        elsif ($op eq "shutdown") { ok_($id, "bye"); exit 0 }
        elsif ($op eq "import") {
            my $t = $msg->{target};
            if (eval "require $t; 1") { ok_($id, { module => $t }) }
            elsif (-f "./$t.pl") { do "./$t.pl"; ok_($id, { file => "./$t.pl" }) }
            else { die "cannot import '$t' (no module, no ./$t.pl)\n" }
        }
        elsif ($op eq "call") {
            my $t = $msg->{target};
            $t =~ s/\./::/g;
            no strict 'refs';
            die "'$t' is not callable\n" unless defined &{$t};
            my @res = &{$t}(deref($msg->{args}));
            ok_($id, to_symbol(@res > 1 ? \@res : $res[0]));
        }
        elsif ($op eq "new") {
            my $cls = $msg->{target};
            $cls =~ s/\./::/g;
            eval "require $cls";
            my $obj = $cls->new(deref($msg->{args}));
            ok_($id, handle_for($obj));
        }
        elsif ($op eq "hcall") {
            my $obj = $HANDLES{$msg->{handle}} or die "stale handle\n";
            my $m = $msg->{method};
            ok_($id, to_symbol($obj->$m(deref($msg->{args}))));
        }
        elsif ($op eq "free") { delete $HANDLES{$msg->{handle}}; ok_($id, 1); }
        elsif ($op eq "exec") {
            %SYM = %{ $msg->{env} // {} };
            *sym = \%SYM;
            my $value = eval "no warnings q(once); our %sym; local *sym = *SYM; $msg->{code}";
            die $@ if $@;
            my %exports = map { $_ => to_symbol($SYM{$_}) } keys %SYM;
            ok_($id, to_symbol($value), \%exports);
        }
        else { die "unknown op '$op'\n" }
    };
    fail_($id, $@) if $@;
}
