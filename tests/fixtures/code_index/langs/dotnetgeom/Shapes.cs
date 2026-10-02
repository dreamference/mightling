namespace Geom;

public interface IFigure
{
    double Area();
}

public class Circle : IFigure
{
    private readonly double radius;

    public Circle(double radius)
    {
        this.radius = radius;
    }

    public double Area()
    {
        return 3.0 * radius * radius;
    }
}

public class Disk : Circle
{
    public Disk(double radius) : base(radius)
    {
    }

    public double Surface()
    {
        return 2.0 * Area();
    }
}

public static class Factory
{
    public static Disk MakeDisk(double radius)
    {
        return new Disk(radius);
    }
}
