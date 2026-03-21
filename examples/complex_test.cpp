#include <iostream>

namespace MyNamespace {
    void say_hello() {
        std::cout << "Hello from MyNamespace" << std::endl;
    }

    class MyClass {
    public:
        void member_func();
    };
}

void MyNamespace::MyClass::member_func() {
    std::cout << "Member func called" << std::endl;
}

int main() {
    MyNamespace::say_hello();
    MyNamespace::MyClass obj;
    obj.member_func();
    return 0;
}
