// A NODE program borrowing Python's statistics, R, Java, PHP, C.
import { sym } from "../clients/node/sym.mjs";

const sales = [984, 1250, 872, 1490, 1105];
const mean = await sym.call("python", "statistics.mean", [sales]);          // Python
const sd   = await sym.request({ op: "call", lang: "r", target: "sd", args: [sales] }); // R
const uuid = await (await (await sym.java("java.util.UUID")).randomUUID()).toString();  // Java
const fmt  = await sym.call("php", "number_format", [mean, 2]);             // PHP
const root = await sym.request({ op: "call", lang: "c", target: "m.sqrt",
                                 args: [mean], ret: "double", argtypes: ["double"] });  // C

console.log(`SALES REPORT ${uuid.slice(0, 8)}`);
console.log(`  mean       = ${fmt}   (Python computed, PHP formatted)`);
console.log(`  sd         = ${sd.toFixed(2)}     (R)`);
console.log(`  sqrt(mean) = ${root.toFixed(3)}     (C libm)`);
console.log("NODE importing Python, R, Java, PHP, C: OK");
await sym.close();
process.exit(0);
