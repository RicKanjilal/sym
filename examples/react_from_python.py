"""React — yes, React — driven from Python through Sym.
Once: (cd examples && npm install react react-dom)
This is React's FUNCTION-SHAPED half: createElement + renderToString.
The browser half (hooks, events, the living app) is an environment,
and environments don't cross bridges — in any system.
"""
import sym

React  = sym._b.import_module("js", "react")
Server = sym._b.import_module("js", "react-dom/server")

page = React.createElement(
    "div", {"className": "report"},
    React.createElement("h1", None, "Sales Report"),
    React.createElement("ul", None,
        React.createElement("li", None, "composed in Python"),
        React.createElement("li", None, "rendered by React in Node"),
        React.createElement("li", None, "brokered by Sym")))

print(Server.renderToString(page))
sym.close()
