package geom;

public class Disk extends Circle {
    public Disk(double radius) {
        super(radius);
    }

    public double surface() {
        return 2.0 * area();
    }

    public static Disk makeDisk(double radius) {
        return new Disk(radius);
    }
}
