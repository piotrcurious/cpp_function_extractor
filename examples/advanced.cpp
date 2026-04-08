namespace Math {
    template <typename T>
    class Matrix {
    public:
        T data[4][4];
        static int count;
        void identity() {
            for(int i=0; i<4; ++i) data[i][i] = 1;
        }
    };

    template <typename T>
    int Matrix<T>::count = 0;
}

int main() {
    Math::Matrix<float> m;
    m.identity();
    return 0;
}
