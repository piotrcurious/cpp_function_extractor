#include <iostream>

int global_var = 42;

struct Point {
    int x, y;
};

void print_point(Point p) {
    std::cout << "Point: (" << p.x << ", " << p.y << ")" << std::endl;
}

int add(int a, int b) {
    return a + b;
}

class Calculator {
public:
    int multiply(int a, int b) {
        return a * b;
    }
};

int main() {
    Point p = {1, 2};
    print_point(p);
    std::cout << "Sum: " << add(3, 4) << std::endl;
    Calculator calc;
    std::cout << "Product: " << calc.multiply(5, 6) << std::endl;
    return 0;
}
