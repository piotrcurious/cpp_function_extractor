namespace App {
    namespace Utils {
        enum class Status { OK, ERROR, PENDING };

        union Value {
            int i;
            float f;
        };

        template <typename T, int Size>
        class Buffer {
        public:
            T data[Size];
            static int count;
            void clear();
        };

        template <typename T, int Size>
        int Buffer<T, Size>::count = 0;

        template <typename T, int Size>
        void Buffer<T, Size>::clear() {
            for(int i=0; i<Size; ++i) data[i] = T();
        }
    }

    class Controller {
    public:
        struct Config {
            int id;
            enum class Type { FAST, SLOW };
            Type type;
        };
        void init(Config c);
        bool operator!() const { return false; }
    };
}

void App::Controller::init(Config c) {
    // implementation
}

template <typename T>
T absolute(T val) {
    return val < 0 ? -val : val;
}

#define APP_VERSION "1.0.0"
#define LOG(x)

int main() {
    App::Utils::Buffer<int, 10> b;
    b.clear();
    return 0;
}
