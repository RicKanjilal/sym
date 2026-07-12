// A JAVA program borrowing Python, R, PHP, Ruby, C.
import java.util.*;

public class java_app {
    public static void main(String[] a) throws Exception {
        Sym sym = new Sym();
        List<Integer> sales = Arrays.asList(984, 1250, 872, 1490, 1105);
        double mean = ((Number) sym.call("python", "statistics.mean", sales)).doubleValue();
        double sd   = ((Number) sym.call("r", "sd", sales)).doubleValue();
        String fmt  = (String) sym.call("php", "number_format", mean, 2);
        String up   = (String) sym.call("ruby", "Kernel.format", "sales report java edition");
        double root = ((Number) sym.cCall("m.sqrt", "double",
                          Arrays.asList("double"), mean)).doubleValue();
        System.out.println(up.toUpperCase());
        System.out.printf("  mean       = %s   (Python via Java)%n", fmt);
        System.out.printf("  sd         = %.2f     (R)%n", sd);
        System.out.printf("  sqrt(mean) = %.3f     (C libm)%n", root);
        System.out.println("JAVA importing Python, R, PHP, Ruby, C: OK");
        sym.close();
    }
}
