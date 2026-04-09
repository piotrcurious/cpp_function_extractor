typedef int MyInt;
using MyFloat = float;

namespace Math {
    typedef double MyDouble;
    using MyLong = long;
}

[[nodiscard]] constexpr int get_magic() {
    return 42;
}

inline void fast_func() {}

int main() {
    return 0;
}
