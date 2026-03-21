#include <iostream>

void foo(int x) {
    std::cout << "foo int: " << x << std::endl;
}

void foo(double x) {
    std::cout << "foo double: " << x << std::endl;
}

int main() {
    foo(1);
    foo(1.0);
    return 0;
}
