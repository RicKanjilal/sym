// ═══════════════════════════════════════════════════════════
//  A RUST program that thinks it can import anything. It can.
//    Python's statistics · R's sd · Java's UUID · PHP · C libm
// ═══════════════════════════════════════════════════════════
include!("../clients/rust/sym_lib.rs");

fn main() {
    let mut sym = Sym::new();
    let sales = vec![984.0, 1250.0, 872.0, 1490.0, 1105.0];
    let jsales: Vec<J> = sales.iter().map(|x| J::num(*x)).collect();

    // Python: statistics.mean — Python owns data science
    let mean = sym.call("python", "statistics.mean", vec![J::A(jsales.clone())])
        .unwrap().as_f64();

    // R: sd — statisticians trust R
    let sd = sym.call("r", "sd", vec![J::A(jsales)]).unwrap().as_f64();

    // Java: a real UUID — enterprise plumbing
    let uuid = sym.call("java", "java.util.UUID.randomUUID", vec![]).unwrap();
    let id = sym.hcall(&uuid, "toString", vec![]).unwrap();

    // Java live object: build the banner in a JVM StringBuilder
    let sb = sym.new_obj("java", "java.lang.StringBuilder",
                         vec![J::s("SALES REPORT ")]).unwrap();
    sym.hcall(&sb, "append", vec![J::s(&id.as_str()[..8])]).unwrap();
    let banner = sym.hcall(&sb, "toString", vec![]).unwrap();

    // PHP: number formatting — the web's cashier
    let pretty = sym.call("php", "number_format",
                          vec![J::num(mean), J::num(2.0)]).unwrap();

    // C: raw sqrt from libm, because we can
    let root = sym.c_call("m.sqrt", "double", vec!["double"],
                          vec![J::num(mean)]).unwrap().as_f64();

    println!("{}", banner.as_str());
    println!("  n          = {}", sales.len());
    println!("  mean       = {}   (Python statistics)", pretty.as_str());
    println!("  sd         = {:.2}     (R)", sd);
    println!("  sqrt(mean) = {:.3}     (C libm)", root);
    println!("RUST importing Python, R, Java, PHP, C: OK");
    sym.close();
}
