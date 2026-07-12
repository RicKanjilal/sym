#!/bin/bash
# The paradigm, demonstrated: TEN languages, each importing libraries
# from ecosystems that aren't theirs. Same report, ten authors.
#   ./run_all.sh
set -e
cd "$(dirname "$0")"
export SYM_HOME="${SYM_HOME:-$HOME/.sym}"
# self-sufficient: `import sym` resolves from the repo, no install needed
export PYTHONPATH="$(cd .. && pwd)/clients/python:$PYTHONPATH"
line() { echo; echo "════ $1 ════"; }

line "SYM (the home language)"
python3 ../bin/sym.py run sym_app.sym

line "PYTHON borrowing R, Java, PHP, Ruby, C"
python3 python_app.py

line "NODE borrowing Python, R, Java, PHP, C"
node node_app.mjs

line "RUBY borrowing Python, R, Java, PHP, C"
ruby ruby_app.rb

line "PHP borrowing Python, R, Java, Ruby, C"
php php_app.php

line "PERL borrowing Python, R, Java, PHP, C"
perl perl_app.pl

line "R borrowing Python, Java, PHP, Ruby, C"
Rscript r_app.R

line "JAVA borrowing Python, R, PHP, Ruby, C"
cp ../clients/java/Sym.java . && javac Sym.java java_app.java
java -cp . java_app
rm -f Sym.java ./*.class

line "GO borrowing Python, R, Java, PHP, C"
mkdir -p /tmp/sym-go-app
sed 's/^func main()/func selftestMain()/' ../clients/go/sym.go > /tmp/sym-go-app/sym.go
sed 's/^func mainApp(sym \*Sym) {/func main() {\n\tsym := NewSym()/' go_app.go > /tmp/sym-go-app/app.go
(cd /tmp/sym-go-app && GOCACHE=/tmp/gocache GO111MODULE=off go build -o /tmp/sym-go-app/app . )
/tmp/sym-go-app/app

line "RUST borrowing Python, R, Java, PHP, C"
rustc -O rust_app.rs -o /tmp/sym-rust-app 2>/dev/null
/tmp/sym-rust-app

echo
echo "TEN LANGUAGES. ONE LIBRARY UNIVERSE. Sym is the host."
