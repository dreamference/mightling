namespace Geom;

public static class Program
{
    public static double Total(double radius)
    {
        Disk disk = Factory.MakeDisk(radius);
        IFigure figure = disk;
        return figure.Area() + disk.Surface();
    }

    public static void Main()
    {
        System.Console.WriteLine(Total(2.0));
    }
}
