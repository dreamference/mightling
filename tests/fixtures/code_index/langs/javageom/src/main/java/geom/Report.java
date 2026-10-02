package geom;

public final class Report {
    private Report() {
    }

    public static double total(double radius) {
        Disk disk = Disk.makeDisk(radius);
        Figure figure = disk;
        return figure.area() + disk.surface();
    }

    public static void main(String[] args) {
        System.out.println(total(2.0));
    }
}
