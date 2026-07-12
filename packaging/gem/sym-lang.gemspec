Gem::Specification.new do |s|
  s.name     = "sym-lang"
  s.version  = "0.2.0"
  s.summary  = "Sym client for Ruby - use any language's libraries from Ruby"
  s.authors  = ["Ric Kanjilal"]
  s.license  = "MIT"
  s.homepage = "https://github.com/RicKanjilal/sym"
  s.files    = ["lib/sym.rb"]
  s.post_install_message = "Sym core needed: pip install sym-lang"
end
# Publish: mkdir lib && cp ../../clients/ruby/sym.rb lib/ && gem build && gem push
