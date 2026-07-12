import { sym } from "../clients/node/sym.mjs";

const Calc = await sym.java("math.Calculator");
console.log("static:", await Calc.add(5, 8));

const AL = await sym.java("java.util.ArrayList");
const lst = await AL.new();
await lst.add("from"); await lst.add("node");
console.log("handle:", await lst.size(), await lst.get(1));

await sym.block("ruby", 'sym["x"] = "ruby via node client"');
console.log("shared:", await sym.get("x"));

console.log("cross:", await sym.call("php", "strrev", ["middleware"]));

await sym.close();
console.log("NODE CLIENT ALIVE");
process.exit(0);
