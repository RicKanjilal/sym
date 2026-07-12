package math;

// Ric's example from the vision doc — Python never knows this exists.
// It talks to Sym. Sym talks to the JVM.
public class Calculator {
    public static int add(int a, int b) { return a + b; }
    public static long fib(int n) {
        long a = 0, b = 1;
        for (int i = 0; i < n; i++) { long t = a + b; a = b; b = t; }
        return a;
    }
    public static double mean(java.util.List<Number> xs) {
        double s = 0; for (Number x : xs) s += x.doubleValue();
        return s / xs.size();
    }
}
